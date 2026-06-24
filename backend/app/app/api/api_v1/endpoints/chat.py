import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.chat_agent import ChatAgent, ChatTurnResult
from app.ai.chat_tools import execute_tool, merge_confirm_tool_args
from app.api import deps
from app.core.config import settings
from app.core.tiers import Tier
from app.schemas.chat import (
    ChatAction,
    ChatConfirmRequest,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatStreamMeta,
    ChatStreamToken,
    ConversationDetail,
    ConversationMessage,
    ConversationSummary,
    ToolStep,
    chat_action_detail,
)
from app.services.entitlements import check_quota
from app.utilities.observability import (
    finalize_turn,
    record_tool_call,
    start_turn,
    upsert_conversation,
)
from app.utilities.redis import (
    delete_conversation,
    delete_pending_action,
    get_conversation,
    get_pending_action,
    list_user_conversations,
    save_conversation,
)
from app.utilities.wide_events import enrich_event, timed


def _trace_ids(request: Request) -> tuple[str | None, str | None]:
    request_id = getattr(request.state, "request_id", None)
    wide_event = getattr(request.state, "wide_event", None)
    trace_id = None
    if isinstance(wide_event, dict):
        trace_id = wide_event.get("trace_id")
    return trace_id, request_id


def _status_for_turn(turn: ChatTurnResult) -> str:
    if turn.max_rounds_reached:
        return "max_rounds"
    if turn.pending_question is not None:
        return "pending_question"
    if turn.pending_confirmation is not None:
        return "pending_confirmation"
    return "ok"

router = APIRouter()
_agent: ChatAgent | None = None


def get_chat_agent() -> ChatAgent:
    global _agent
    if _agent is None:
        _agent = ChatAgent()
    return _agent


def _require_chat_enabled() -> None:
    if not settings.CHAT_ENABLED:
        raise HTTPException(status_code=503, detail="Chat is disabled")


async def _enforce_quota(db: AsyncSession, user: models.User) -> tuple[bool, Tier, dict]:
    """Check the user's subscription quota before a chat turn.

    Returns (allowed, tier, usage_dict). Raises HTTPException(402) when over
    the message/token cap so the client can prompt an upgrade.
    """
    quota = await check_quota(db, user)
    if not quota.allowed:
        raise HTTPException(
            status_code=402,
            detail={
                "reason": quota.reason or "quota_exceeded",
                "usage": quota.usage.model_dump(mode="json"),
                "tier": quota.tier.value,
                "upgrade_url": "/account",
            },
        )
    return True, quota.tier, quota.usage.model_dump(mode="json")


def _conversation_title(message: str) -> str:
    text = message.strip().replace("\n", " ")
    return text[:60] + ("…" if len(text) > 60 else "")


def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _turn_to_response(
    conversation_id: str, turn: ChatTurnResult
) -> ChatMessageResponse:
    return ChatMessageResponse(
        conversation_id=conversation_id,
        reply=turn.reply,
        actions=turn.actions,
        render=turn.render,
        tool_steps=turn.tool_steps,
        pending_confirmation=turn.pending_confirmation,
        pending_question=turn.pending_question,
    )


def _merge_messages(
    existing: list[dict], new_messages: list[dict]
) -> list[dict]:
    merged = list(existing)
    for msg in new_messages:
        entry = {
            "role": msg["role"],
            "content": msg.get("content") or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        merged.append(entry)
    return merged


@router.post("/messages", response_model=ChatMessageResponse)
async def send_chat_message(
    request: Request,
    body: ChatMessageRequest,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    agent: ChatAgent = Depends(get_chat_agent),
) -> Any:
    _require_chat_enabled()
    _, tier, _usage = await _enforce_quota(db, current_user)

    conversation_id = body.conversation_id or str(uuid.uuid4())
    history: list[dict] = []
    title = _conversation_title(body.message)

    existing = await get_conversation(conversation_id, current_user.id)
    if existing:
        history = existing.get("messages") or []
        title = existing.get("title") or title

    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "subscription_tier": tier.value,
        },
        operation={
            "type": "chat_message",
            "conversation_id": conversation_id,
            "message_length": len(body.message),
        },
    )

    trace_id, request_id = _trace_ids(request)
    turn_id = await start_turn(
        conversation_id=conversation_id,
        user_id=current_user.id,
        user_message=body.message,
        model=agent.model,
        trace_id=trace_id,
        request_id=request_id,
    )

    with timed() as t:
        turn = await agent.run_turn(
            db,
            current_user,
            body.message,
            history,
            conversation_id=conversation_id,
            turn_id=turn_id,
            tier=tier,
        )

    await finalize_turn(
        turn_id,
        status=_status_for_turn(turn),
        assistant_reply=turn.reply,
        duration_ms=t.ms,
        rounds_count=turn.rounds_count,
        actions_count=len(turn.actions),
        pending_confirmation=turn.pending_confirmation is not None,
        pending_question=turn.pending_question is not None,
        total_prompt_tokens=turn.total_prompt_tokens,
        total_completion_tokens=turn.total_completion_tokens,
        total_tokens=turn.total_tokens,
        total_cost_usd=turn.total_cost_usd,
    )

    enrich_event(
        request,
        ai={
            "provider": "openrouter",
            "duration_ms": t.ms,
            "actions_count": len(turn.actions),
            "pending_confirmation": turn.pending_confirmation is not None,
        },
    )

    updated_messages = _merge_messages(history, turn.new_messages)
    await save_conversation(
        conversation_id,
        current_user.id,
        updated_messages,
        title,
        settings.CHAT_CONVERSATION_TTL_SECONDS,
    )
    await upsert_conversation(
        conversation_id, current_user.id, title, len(updated_messages)
    )

    return _turn_to_response(conversation_id, turn)


@router.post("/messages/stream")
async def send_chat_message_stream(
    request: Request,
    body: ChatMessageRequest,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    agent: ChatAgent = Depends(get_chat_agent),
) -> StreamingResponse:
    _require_chat_enabled()
    # Quota is checked before returning the StreamingResponse so a 402 still
    # surfaces as a normal JSON error (once streaming starts we can't change
    # the status code).
    _, tier, _usage = await _enforce_quota(db, current_user)

    conversation_id = body.conversation_id or str(uuid.uuid4())
    history: list[dict] = []
    title = _conversation_title(body.message)

    existing = await get_conversation(conversation_id, current_user.id)
    if existing:
        history = existing.get("messages") or []
        title = existing.get("title") or title

    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "subscription_tier": tier.value,
        },
        operation={
            "type": "chat_message_stream",
            "conversation_id": conversation_id,
            "message_length": len(body.message),
        },
    )

    trace_id, request_id = _trace_ids(request)
    turn_id = await start_turn(
        conversation_id=conversation_id,
        user_id=current_user.id,
        user_message=body.message,
        model=agent.model,
        trace_id=trace_id,
        request_id=request_id,
    )

    async def event_generator() -> AsyncIterator[str]:
        with timed() as t:
            yield _sse_event(
                "meta",
                ChatStreamMeta(conversation_id=conversation_id).model_dump(),
            )
            try:
                async for event in agent.stream_turn(
                    db,
                    current_user,
                    body.message,
                    history,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    tier=tier,
                ):
                    if event.type == "token" and event.delta:
                        yield _sse_event(
                            "token",
                            ChatStreamToken(delta=event.delta).model_dump(),
                        )
                    elif event.type == "tool_step" and event.tool_step:
                        yield _sse_event(
                            "tool_step", event.tool_step.model_dump()
                        )
                    elif event.type == "done" and event.result:
                        turn = event.result
                        updated_messages = _merge_messages(
                            history, turn.new_messages
                        )
                        await save_conversation(
                            conversation_id,
                            current_user.id,
                            updated_messages,
                            title,
                            settings.CHAT_CONVERSATION_TTL_SECONDS,
                        )
                        await upsert_conversation(
                            conversation_id,
                            current_user.id,
                            title,
                            len(updated_messages),
                        )
                        await finalize_turn(
                            turn_id,
                            status=_status_for_turn(turn),
                            assistant_reply=turn.reply,
                            duration_ms=t.ms,
                            rounds_count=turn.rounds_count,
                            actions_count=len(turn.actions),
                            pending_confirmation=turn.pending_confirmation
                            is not None,
                            pending_question=turn.pending_question is not None,
                            total_prompt_tokens=turn.total_prompt_tokens,
                            total_completion_tokens=turn.total_completion_tokens,
                            total_tokens=turn.total_tokens,
                            total_cost_usd=turn.total_cost_usd,
                        )
                        response = _turn_to_response(conversation_id, turn)
                        enrich_event(
                            request,
                            ai={
                                "provider": "openrouter",
                                "duration_ms": t.ms,
                                "actions_count": len(turn.actions),
                                "pending_confirmation": turn.pending_confirmation
                                is not None,
                                "streaming": True,
                            },
                        )
                        yield _sse_event("done", response.model_dump(mode="json"))
            except Exception as exc:
                await finalize_turn(
                    turn_id,
                    status="error",
                    error=str(exc),
                    duration_ms=t.ms,
                )
                enrich_event(
                    request,
                    ai={
                        "provider": "openrouter",
                        "duration_ms": t.ms,
                        "streaming": True,
                        "error": str(exc),
                    },
                )
                yield _sse_event("error", {"detail": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    _require_chat_enabled()
    rows = await list_user_conversations(current_user.id)
    return [
        ConversationSummary(
            id=r["id"],
            title=r.get("title") or "Conversation",
            updated_at=datetime.fromisoformat(r["updated_at"])
            if r.get("updated_at")
            else datetime.now(timezone.utc),
            last_message_preview=r.get("last_message_preview"),
        )
        for r in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation_detail(
    conversation_id: str,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    _require_chat_enabled()
    conv = await get_conversation(conversation_id, current_user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = [
        ConversationMessage(
            role=m["role"],
            content=m.get("content", ""),
            created_at=datetime.fromisoformat(m["created_at"])
            if m.get("created_at")
            else None,
        )
        for m in conv.get("messages", [])
    ]
    updated = conv.get("updated_at")
    return ConversationDetail(
        id=conversation_id,
        title=conv.get("title") or "Conversation",
        messages=messages,
        updated_at=datetime.fromisoformat(updated)
        if updated
        else datetime.now(timezone.utc),
    )


@router.delete("/conversations/{conversation_id}")
async def remove_conversation(
    conversation_id: str,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> dict[str, bool]:
    _require_chat_enabled()
    ok = await delete_conversation(conversation_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.post(
    "/conversations/{conversation_id}/confirm",
    response_model=ChatMessageResponse,
)
async def confirm_chat_action(
    request: Request,
    conversation_id: str,
    body: ChatConfirmRequest,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    _require_chat_enabled()
    await _enforce_quota(db, current_user)

    pending = await get_pending_action(body.action_id, current_user.id)
    if not pending:
        raise HTTPException(status_code=404, detail="Pending action not found or expired")
    if pending.get("conversation_id") != conversation_id:
        raise HTTPException(status_code=400, detail="Action does not belong to this conversation")

    enrich_event(
        request,
        user={"id": current_user.id},
        operation={
            "type": "chat_confirm",
            "tool_name": pending.get("tool_name"),
            "action_id": body.action_id,
        },
    )

    tool_args = merge_confirm_tool_args(
        pending["tool_name"],
        pending["tool_args"],
        body.tool_args,
    )

    orig_turn_id = pending.get("turn_id")

    with timed() as t:
        result, action_type = await execute_tool(
            pending["tool_name"],
            tool_args,
            db,
            current_user,
            confirmed=True,
        )

    await delete_pending_action(body.action_id)

    success = bool(result.get("success"))
    await record_tool_call(
        turn_id=orig_turn_id,
        round_index=0,
        tool_name=pending["tool_name"],
        arguments=tool_args,
        result=result,
        status="completed" if success else "failed",
        duration_ms=t.ms,
        error=None if success else result.get("error"),
    )
    await finalize_turn(
        orig_turn_id,
        status="ok" if success else "error",
        pending_confirmation=False,
        error=None if success else result.get("error"),
    )

    enrich_event(
        request,
        ai={"provider": "openrouter", "duration_ms": t.ms, "confirmed": True},
    )

    actions: list[ChatAction] = []
    if action_type and result.get("success"):
        actions.append(
            ChatAction(
                type=action_type,
                detail=chat_action_detail(result.get("data")),
            )
        )
        reply = f"Done. {action_type.replace('_', ' ')} completed successfully."
    else:
        reply = result.get("error") or "The action could not be completed."

    conv = await get_conversation(conversation_id, current_user.id)
    history = conv.get("messages", []) if conv else []
    confirm_user_msg = {
        "role": "user",
        "content": f"[Confirmed] {pending['tool_name']}",
    }
    confirm_assistant_msg = {"role": "assistant", "content": reply}
    updated_messages = _merge_messages(
        history, [confirm_user_msg, confirm_assistant_msg]
    )
    title = conv.get("title") if conv else "Conversation"
    await save_conversation(
        conversation_id,
        current_user.id,
        updated_messages,
        title,
        settings.CHAT_CONVERSATION_TTL_SECONDS,
    )
    await upsert_conversation(
        conversation_id, current_user.id, title, len(updated_messages)
    )

    return ChatMessageResponse(
        conversation_id=conversation_id,
        reply=reply,
        actions=actions,
        tool_steps=[
            ToolStep(
                tool_name=pending["tool_name"],
                status="completed",
                label=f"Confirmed {pending['tool_name'].replace('_', ' ')}",
            )
        ],
        pending_confirmation=None,
    )
