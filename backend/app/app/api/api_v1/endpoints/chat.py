import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import datetime, timezone
from functools import wraps
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.usage import RunUsage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import (
    ChatDeps,
    build_model,
    build_obs_hooks,
    build_toolset,
    build_turn_budget,
    build_usage_limits,
)
from app.ai.agent import get_chat_agent as get_pydantic_agent
from app.ai.chat_presentation import (
    append_confirmation_to_model_history,
    deferred_question_results,
    latest_user_request,
    load_model_messages,
    public_chat_stream,
    public_ui_messages,
    serialize_model_messages,
)
from app.ai.chat_tools import (
    PROMPT_VERSION,
    execute_tool,
    format_confirmation_result,
    merge_confirm_tool_args,
)
from app.api import deps
from app.core.config import settings
from app.core.tiers import Tier, chat_model_for_tier
from app.models.user import User
from app.schemas.chat import (
    ChatAction,
    ChatConfirmRequest,
    ChatMessageResponse,
    ConversationDetail,
    ConversationMessage,
    ConversationSummary,
    chat_action_detail,
)
from app.schemas.subscription import UsageSummaryOut
from app.services.entitlements import (
    TURN_STATUS_INTERRUPTED,
    QuotaReservationError,
    QuotaResult,
    check_quota,
    reserve_chat_turn,
)
from app.utilities.observability import (
    finalize_turn,
    record_tool_call,
    upsert_conversation,
)
from app.utilities.redis import (
    CHAT_CONVERSATION_LOCK_TTL,
    acquire_conversation_lock,
    delete_conversation,
    delete_pending_action,
    get_conversation,
    get_pending_action,
    hit_rate_limit,
    list_user_conversations,
    refresh_conversation_lock,
    release_conversation_lock,
    save_conversation,
    validate_conversation_id,
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


def _require_valid_conversation_id(conversation_id: str) -> str:
    try:
        return validate_conversation_id(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _acquire_chat_lock(user_id: int, conversation_id: str) -> str:
    try:
        token = await acquire_conversation_lock(user_id, conversation_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail={"reason": "conversation_lock_unavailable"}
        ) from exc
    if token is None:
        raise HTTPException(
            status_code=409,
            detail={"reason": "conversation_busy"},
        )
    return token


def _serialized_conversation_endpoint(
    endpoint: Callable[..., Any],
) -> Callable[..., Any]:
    """Serialize a non-streaming endpoint and always release its lock."""

    @wraps(endpoint)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        conversation_id = _require_valid_conversation_id(kwargs["conversation_id"])
        kwargs["conversation_id"] = conversation_id
        user = kwargs["current_user"]
        lock_token = await _acquire_chat_lock(user.id, conversation_id)
        try:
            return await endpoint(*args, **kwargs)
        finally:
            with suppress(Exception):
                await release_conversation_lock(
                    user.id, conversation_id, lock_token
                )

    return wrapped


def _release_stream_lock_on_setup_error(
    endpoint: Callable[..., Any],
) -> Callable[..., Any]:
    """Release a streaming lock when setup fails before iteration owns it."""

    @wraps(endpoint)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        request = kwargs["request"]
        try:
            return await endpoint(*args, **kwargs)
        except BaseException:
            lock = getattr(request.state, "chat_stream_lock", None)
            if lock:
                user_id, conversation_id, token = lock
                with suppress(Exception):
                    await release_conversation_lock(
                        user_id, conversation_id, token
                    )
                request.state.chat_stream_lock = None
            raise

    return wrapped


async def _enforce_chat_rate_limit(user_id: int) -> None:
    """Throttle model-backed chat requests per user (fails closed)."""
    windows = (
        (60, settings.CHAT_RATE_LIMIT_PER_MINUTE),
        (86_400, settings.CHAT_RATE_LIMIT_PER_DAY),
    )
    for window_seconds, limit in windows:
        if limit <= 0:
            continue
        try:
            hits, retry_after = await hit_rate_limit(f"chat:{user_id}", window_seconds)
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail={"reason": "rate_limit_unavailable"}
            ) from exc
        if hits > limit:
            raise HTTPException(
                status_code=429,
                detail={"reason": "rate_limited", "retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )


def _usage_payload(quota: QuotaResult) -> dict[str, Any]:
    """Client-facing usage: the share of the token limit used, not raw counts."""
    return UsageSummaryOut.model_validate(quota.usage).model_dump(mode="json")


async def _enforce_quota(
    db: AsyncSession, user: User
) -> tuple[bool, Tier, dict[str, Any]]:
    """Check the user's subscription quota before a chat turn.

    Returns (allowed, tier, usage_dict). Raises HTTPException(402) when over
    the token cap so the client can prompt an upgrade.
    """
    quota = await check_quota(db, user)
    if not quota.allowed:
        raise HTTPException(
            status_code=402,
            detail={
                "reason": quota.reason or "quota_exceeded",
                "usage": _usage_payload(quota),
                "tier": quota.tier.value,
                "upgrade_url": "/account",
            },
        )
    return True, quota.tier, _usage_payload(quota)


def _conversation_title(message: str) -> str:
    text = message.strip().replace("\n", " ")
    return text[:60] + ("…" if len(text) > 60 else "")


def _resolve_client_context(payload: Any) -> tuple[str, str, datetime]:
    context = payload.get("client_context") if isinstance(payload, dict) else None
    context = context if isinstance(context, dict) else {}
    locale = str(context.get("locale") or "en")[:32]
    requested_zone = str(context.get("time_zone") or "UTC")[:64]
    try:
        zone = ZoneInfo(requested_zone)
        time_zone_name = requested_zone
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
        time_zone_name = "UTC"
    return locale, time_zone_name, datetime.now(zone)


# Vercel AI SDK UI Message Stream Protocol endpoint (Pydantic AI + VercelAIAdapter)
#
# This single POST /chat endpoint replaces the legacy /messages and
# /messages/stream endpoints for frontends using `@ai-sdk/react`'s `useChat`.
# It speaks a public subset of the Vercel AI data-stream protocol. Raw tool and
# reasoning events stay inside the server-side harness.
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
@_release_stream_lock_on_setup_error
async def chat_vercel(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.require_chat_access),
) -> Response:
    """Vercel AI data-stream chat endpoint (sdk_version=6).

    Quota is enforced before the stream starts so a 402 still surfaces as a
    normal JSON error. Once streaming begins, the VercelAIAdapter owns the SSE
    response and emits the protocol chunks that `useChat` consumes.
    """
    await _enforce_chat_rate_limit(current_user.id)
    agent = get_pydantic_agent()
    try:
        request_payload = await request.json()
    except Exception:
        request_payload = {}
    locale, time_zone_name, local_now = _resolve_client_context(request_payload)
    adapter = await VercelAIAdapter.from_request(request, agent=agent, sdk_version=6)

    conversation_id = _require_valid_conversation_id(
        adapter.conversation_id or str(uuid.uuid4())
    )
    current_messages = latest_user_request(adapter.messages)
    # ``messages`` is a cached property used by run_stream_native. Replacing the
    # cached value makes older clients that still submit full UI history safe:
    # server-owned history is authoritative and only the newest user turn is
    # appended.
    adapter.__dict__["messages"] = current_messages
    user_msg = _last_user_text(current_messages)
    title = _conversation_title(user_msg) if user_msg else "Conversation"

    trace_id, request_id = _trace_ids(request)
    lock_token = await _acquire_chat_lock(current_user.id, conversation_id)
    request.state.chat_stream_lock = (
        current_user.id,
        conversation_id,
        lock_token,
    )
    try:
        quota, turn_id = await reserve_chat_turn(
            db,
            current_user,
            conversation_id=conversation_id,
            user_message=user_msg,
            trace_id=trace_id,
            request_id=request_id,
        )
    except QuotaReservationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"reason": "quota_unavailable"},
        ) from exc
    if not quota.allowed:
        raise HTTPException(
            status_code=402,
            detail={
                "reason": quota.reason or "quota_exceeded",
                "usage": _usage_payload(quota),
                "tier": quota.tier.value,
                "upgrade_url": "/account",
            },
        )
    tier = quota.tier
    stored_conversation = await get_conversation(conversation_id, current_user.id)
    model_history = load_model_messages(stored_conversation)
    deferred_results = deferred_question_results(model_history, user_msg)
    if stored_conversation and stored_conversation.get("title"):
        title = str(stored_conversation["title"])

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

    deps = ChatDeps(
        db=db,
        user=current_user,
        tier=tier,
        conversation_id=conversation_id,
        local_date=local_now.date(),
        time_zone=time_zone_name,
        locale=locale,
        turn_id=turn_id,
    )
    # Pydantic AI accumulates into this object, including the usage of a call
    # the run abandoned mid-stream (a disconnect or an error), which the
    # ``after_model_request`` hook never sees. Failed turns are billed from it.
    run_usage = RunUsage()
    started = time.perf_counter()
    completed = False
    failure_reason: str | None = None

    async def on_complete(result: AgentRunResult) -> None:
        """Persist conversation + finalize observability after a successful run."""
        nonlocal completed, failure_reason
        duration_ms = int((time.perf_counter() - started) * 1000)
        is_pending = deps.had_pending_confirmation
        has_pending_question = deps.pending_question is not None
        usage = result.usage
        all_msgs = result.all_messages()
        assistant_reply = _last_assistant_text(all_msgs)
        status = (
            "pending_confirmation"
            if is_pending
            else "pending_question"
            if has_pending_question
            else "ok"
        )
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
        ui_msgs = public_ui_messages(all_msgs)
        if deps.pending_question:
            ui_msgs.append(
                {
                    "id": deps.pending_question_id or str(uuid.uuid4()),
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "data-pending-question",
                            "data": deps.pending_question,
                        }
                    ],
                }
            )
        private_msgs = serialize_model_messages(all_msgs)
        if not await refresh_conversation_lock(
            current_user.id,
            conversation_id,
            lock_token,
        ):
            raise RuntimeError("conversation_lock_lost")
        saved = await save_conversation(
            conversation_id,
            current_user.id,
            ui_msgs,
            title,
            settings.CHAT_CONVERSATION_TTL_SECONDS,
            model_messages=private_msgs,
        )
        if not saved:
            # ``metered_stream`` finalizes the turn (with its token usage).
            failure_reason = "conversation_persistence_failed"
            enrich_event(
                request,
                persistence={
                    "conversation_saved": False,
                    "pending_confirmation": is_pending,
                },
            )
            raise RuntimeError("conversation_persistence_failed")
        await upsert_conversation(
            conversation_id, current_user.id, title, len(ui_msgs)
        )
        completed = True
        enrich_event(
            request,
            ai={
                "provider": "openrouter",
                "duration_ms": duration_ms,
                "pending_confirmation": is_pending,
                "streaming": True,
                "protocol": "vercel-ai",
                "prompt_version": PROMPT_VERSION,
                "locale": locale,
                "response_language": "es"
                if locale.lower().startswith("es")
                else "en",
                "time_zone": time_zone_name,
            },
        )

    event_stream = adapter.run_stream(
        output_type=[str, DeferredToolRequests],
        deps=deps,
        message_history=model_history,
        deferred_tool_results=deferred_results,
        usage_limits=build_usage_limits(),
        usage=run_usage,
        model=build_model(chat_model_for_tier(tier)),
        toolsets=[build_toolset()],
        capabilities=[build_obs_hooks(), build_turn_budget()],
        on_complete=on_complete,
    )

    async def metered_stream() -> AsyncIterator[Any]:
        async def keep_lock_alive() -> None:
            while True:
                await asyncio.sleep(CHAT_CONVERSATION_LOCK_TTL / 3)
                try:
                    refreshed = await refresh_conversation_lock(
                        current_user.id,
                        conversation_id,
                        lock_token,
                    )
                except Exception:
                    return
                if not refreshed:
                    return

        renewal_task = asyncio.create_task(keep_lock_alive())
        interrupted = False
        try:
            async for event in public_chat_stream(event_stream):
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            # The client disconnected. Whatever it already received was paid
            # for, so this is billed as a message, not refunded as an error.
            interrupted = True
            raise
        finally:
            # After a disconnect the server's cancel scope would cancel every
            # await below, skipping the lock release and the billing update.
            with anyio.CancelScope(shield=True):
                renewal_task.cancel()
                with suppress(asyncio.CancelledError):
                    await renewal_task
                with suppress(Exception):
                    await release_conversation_lock(
                        current_user.id, conversation_id, lock_token
                    )
                request.state.chat_stream_lock = None
                if not completed:
                    await finalize_turn(
                        turn_id,
                        status=TURN_STATUS_INTERRUPTED if interrupted else "error",
                        error=(
                            "stream_interrupted"
                            if interrupted
                            else failure_reason or "run_failed"
                        ),
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        total_prompt_tokens=run_usage.input_tokens,
                        total_completion_tokens=run_usage.output_tokens,
                        total_tokens=run_usage.total_tokens,
                    )

    return adapter.streaming_response(metered_stream())


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    current_user: User = Depends(deps.require_chat_access),
) -> Any:
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
    current_user: User = Depends(deps.require_chat_access),
) -> Any:
    conversation_id = _require_valid_conversation_id(conversation_id)
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
@_serialized_conversation_endpoint
async def remove_conversation(
    conversation_id: str,
    current_user: User = Depends(deps.require_chat_access),
) -> dict[str, bool]:
    conversation_id = _require_valid_conversation_id(conversation_id)
    ok = await delete_conversation(conversation_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.post(
    "/conversations/{conversation_id}/confirm",
    response_model=ChatMessageResponse,
)
@_serialized_conversation_endpoint
async def confirm_chat_action(
    request: Request,
    conversation_id: str,
    body: ChatConfirmRequest,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.require_chat_access),
) -> Any:
    conversation_id = _require_valid_conversation_id(conversation_id)
    await _enforce_quota(db, current_user)

    pending = await get_pending_action(body.action_id, current_user.id)
    if not pending:
        raise HTTPException(
            status_code=404, detail="Pending action not found or expired"
        )
    if pending.get("conversation_id") != conversation_id:
        raise HTTPException(
            status_code=400, detail="Action does not belong to this conversation"
        )

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
    response_language = str(pending.get("response_language") or "en")
    tool_args.setdefault("response_language", response_language)

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
    # The model turn that proposed this action already ran and is billed; a
    # failed confirmation must not flip it to "error" and refund the message.
    await finalize_turn(
        orig_turn_id,
        status="ok",
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
        reply = format_confirmation_result(
            success=True, response_language=response_language
        )
    else:
        reply = format_confirmation_result(
            success=False,
            response_language=response_language,
            error=str(result.get("error")) if result.get("error") else None,
        )

    conv = await get_conversation(conversation_id, current_user.id)
    model_history = load_model_messages(conv)
    updated_model_history = append_confirmation_to_model_history(
        model_history,
        tool_name=pending["tool_name"],
        reply=reply,
    )
    updated_messages = public_ui_messages(updated_model_history)
    title = str(conv.get("title") or "Conversation") if conv else "Conversation"
    saved = await save_conversation(
        conversation_id,
        current_user.id,
        updated_messages,
        title,
        settings.CHAT_CONVERSATION_TTL_SECONDS,
        model_messages=serialize_model_messages(updated_model_history),
    )
    if not saved:
        enrich_event(
            request,
            persistence={
                "conversation_saved": False,
                "confirmed_mutation_may_be_committed": success,
            },
        )
        # Plan 006 will make confirmed mutations replay-safe. Until then, make
        # the persistence failure explicit and do not claim safe retryability.
        raise HTTPException(
            status_code=503,
            detail={"reason": "conversation_persistence_failed"},
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
        pending_confirmation=None,
        ui_message=ui_msg,
    )
