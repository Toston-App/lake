import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.agent import ChatDeps, build_obs_hooks, build_toolset
from app.ai.agent import get_chat_agent as get_pydantic_agent
from app.ai.chat_tools import execute_tool, merge_confirm_tool_args
from app.api import deps
from app.core.config import settings
from app.core.tiers import Tier
from app.schemas.chat import (
    ChatAction,
    ChatConfirmRequest,
    ChatMessageResponse,
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


router = APIRouter()


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




# Vercel AI SDK UI Message Stream Protocol endpoint (Pydantic AI + VercelAIAdapter)
#
# This single POST /chat endpoint replaces the legacy /messages and
# /messages/stream endpoints for frontends using `@ai-sdk/react`'s `useChat`.
# It speaks the Vercel AI data-stream protocol (SSE chunks: text-start/delta/end,
# tool-input/output, start-step/finish-step, finish, tool-approval-request [v6]).
# The legacy endpoints below are kept for the not-yet-migrated frontend.
# --------------------------------------------------------------------------- #


def _last_user_text(messages: list[Any]) -> str:
    """Extract the most recent user prompt text from the adapter's messages."""
    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    return ""


def _last_assistant_text(messages: list[Any]) -> str:
    """Extract the most recent assistant text part from the run result."""
    for msg in reversed(messages):
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    return part.content
    return ""


@router.post("")
async def chat_vercel(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Response:
    """Vercel AI data-stream chat endpoint (sdk_version=6).

    Quota is enforced before the stream starts so a 402 still surfaces as a
    normal JSON error. Once streaming begins, the VercelAIAdapter owns the SSE
    response and emits the protocol chunks that `useChat` consumes.
    """
    _require_chat_enabled()
    _, tier, _usage = await _enforce_quota(db, current_user)

    agent = get_pydantic_agent()
    adapter = await VercelAIAdapter.from_request(
        request, agent=agent, sdk_version=6
    )

    conversation_id = adapter.conversation_id or str(uuid.uuid4())
    user_msg = _last_user_text(adapter.messages)
    title = _conversation_title(user_msg) if user_msg else "Conversation"

    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "subscription_tier": tier.value,
        },
        operation={
            "type": "chat_vercel",
            "conversation_id": conversation_id,
            "message_length": len(user_msg),
        },
    )

    trace_id, request_id = _trace_ids(request)
    turn_id = await start_turn(
        conversation_id=conversation_id,
        user_id=current_user.id,
        user_message=user_msg,
        model=settings.CHAT_MODEL or settings.OPENROUTER_MODEL,
        trace_id=trace_id,
        request_id=request_id,
    )

    deps = ChatDeps(
        db=db,
        user=current_user,
        tier=tier,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    started = time.perf_counter()

    async def on_complete(result: AgentRunResult) -> None:
        """Persist conversation + finalize observability after a successful run."""
        duration_ms = int((time.perf_counter() - started) * 1000)
        is_pending = deps.had_pending_confirmation
        usage = result.usage
        all_msgs = result.all_messages()
        assistant_reply = _last_assistant_text(all_msgs)
        status = "pending_confirmation" if is_pending else "ok"
        await finalize_turn(
            turn_id,
            status=status,
            assistant_reply=assistant_reply,
            duration_ms=duration_ms,
            pending_confirmation=is_pending,
            total_prompt_tokens=usage.input_tokens,
            total_completion_tokens=usage.output_tokens,
            total_tokens=usage.input_tokens + usage.output_tokens,
        )
        try:
            ui_msgs = VercelAIAdapter.dump_messages(all_msgs, sdk_version=6)
            await save_conversation(
                conversation_id,
                current_user.id,
                [m.model_dump() for m in ui_msgs],
                title,
                settings.CHAT_CONVERSATION_TTL_SECONDS,
            )
            await upsert_conversation(
                conversation_id, current_user.id, title, len(ui_msgs)
            )
        except Exception:
            # Persistence is best-effort; never break the stream on a Redis hiccup.
            pass
        enrich_event(
            request,
            ai={
                "provider": "openrouter",
                "duration_ms": duration_ms,
                "pending_confirmation": is_pending,
                "streaming": True,
                "protocol": "vercel-ai",
            },
        )

    event_stream = adapter.run_stream(
        deps=deps,
        toolsets=[build_toolset(tier)],
        capabilities=[build_obs_hooks()],
        on_complete=on_complete,
    )
    return adapter.streaming_response(event_stream)


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

    raw_messages = conv.get("messages", [])
    ui_messages: list[dict[str, Any]] = []
    messages: list[ConversationMessage] = []

    # Conversations may mix UI-format messages ({id, role, parts}, saved by the
    # Pydantic AI flow) and legacy ones ({role, content, created_at}). Handle
    # each message by its own shape so the sidebar + detail view stay correct
    # during the transient mixed-format window after a /confirm.
    for m in raw_messages:
        if not isinstance(m, dict):
            continue
        if "parts" in m:
            ui_messages.append(m)
            messages.append(
                ConversationMessage(
                    role=m.get("role", "assistant"),
                    content=_ui_message_text(m),
                    created_at=None,
                )
            )
        else:
            messages.append(
                ConversationMessage(
                    role=m.get("role", "assistant"),
                    content=m.get("content", ""),
                    created_at=datetime.fromisoformat(m["created_at"])
                    if m.get("created_at")
                    else None,
                )
            )

    updated = conv.get("updated_at")
    return ConversationDetail(
        id=conversation_id,
        title=conv.get("title") or "Conversation",
        messages=messages,
        ui_messages=ui_messages or None,
        updated_at=datetime.fromisoformat(updated)
        if updated
        else datetime.now(timezone.utc),
    )


def _ui_message_text(ui_message: dict[str, Any]) -> str:
    """Flatten text parts of a Vercel AI UI message into a single string."""
    parts = ui_message.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text")
            if text:
                chunks.append(str(text))
    return "".join(chunks)



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

    print(f"🚀 [CONFIRM_CHAT_ACTION] Confirming tool {pending['tool_name']} with args {tool_args} for user {current_user.id} in conversation {conversation_id}")

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

    ui_msg = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "parts": [{"type": "text", "text": reply}],
    }

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
        ui_message=ui_msg,
    )
