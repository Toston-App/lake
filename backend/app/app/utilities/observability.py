"""Chat observability recorder.

Writes per-turn / per-LLM-round / per-tool-call telemetry to Postgres so the
team can build a dashboard over what users generate, token & cost consumption,
tool usage, and errors.

Design rules:
- NEVER break user-facing chat. Every public function is fire-and-forget:
  it catches its own exceptions and logs them. A failed observability write
  must not raise into the agent loop.
- Uses a dedicated async session (``app.db.session.async_session``) so writes
  commit independently of the request's DB transaction -- a rolled-back
  request transaction won't roll back observability, and vice-versa.
- Disabled entirely when ``CHAT_OBSERVABILITY_ENABLED=false``; the functions
  become cheap no-ops returning None.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql import select

from app.core.config import settings
from app.db.session import async_session
from app.models.chat_observability import (
    ChatModelPricing,
    ChatObsConversation,
    ChatObsLLMCall,
    ChatObsToolCall,
    ChatObsTurn,
)

logger = logging.getLogger(__name__)

_PROVIDER = "openrouter"


def _enabled() -> bool:
    return bool(settings.CHAT_OBSERVABILITY_ENABLED)


async def upsert_conversation(
    conversation_id: str,
    user_id: int,
    title: Optional[str] = None,
    message_count: Optional[int] = None,
) -> None:
    if not _enabled() or not conversation_id:
        return
    try:
        async with async_session() as db:
            stmt = insert(ChatObsConversation).values(
                id=conversation_id,
                user_id=user_id,
                title=title,
                message_count=message_count if message_count is not None else 0,
            )
            do_update = stmt.on_conflict_do_update(
                index_elements=[ChatObsConversation.id],
                set_={
                    "title": title if title is not None else ChatObsConversation.title,
                    "message_count": (
                        message_count
                        if message_count is not None
                        else ChatObsConversation.message_count
                    ),
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await db.execute(do_update)
            await db.commit()
    except Exception:
        logger.exception("observability: failed to upsert conversation %s", conversation_id)


async def start_turn(
    *,
    conversation_id: str,
    user_id: int,
    user_message: str,
    model: str,
    trace_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Optional[int]:
    """Create a turn row and return its id, or None if disabled/failed."""
    if not _enabled():
        return None
    try:
        async with async_session() as db:
            turn = ChatObsTurn(
                **{
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "user_message": user_message,
                    "model": model,
                    "provider": _PROVIDER,
                    "status": "ok",
                    "trace_id": trace_id,
                    "request_id": request_id,
                }
            )
            db.add(turn)
            await db.commit()
            await db.refresh(turn)
            return int(turn.id)
    except Exception:
        logger.exception("observability: failed to start turn")
        return None


async def record_llm_call(
    *,
    turn_id: Optional[int],
    round_index: int,
    model: str,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    cached_tokens: Optional[int] = None,
    cost_usd: Optional[float] = None,
    duration_ms: Optional[int] = None,
    finish_reason: Optional[str] = None,
    had_tool_calls: bool = False,
) -> None:
    if not _enabled() or turn_id is None:
        return
    if cost_usd is None:
        cost_usd = await _compute_cost_from_pricing(
            model=model,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            cached_tokens=cached_tokens,
        )
    try:
        async with async_session() as db:
            db.add(
                ChatObsLLMCall(
                    **{
                        "turn_id": turn_id,
                        "round_index": round_index,
                        "model": model,
                        "provider": _PROVIDER,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "cached_tokens": cached_tokens,
                        "cost_usd": cost_usd,
                        "duration_ms": duration_ms,
                        "finish_reason": finish_reason,
                        "had_tool_calls": had_tool_calls,
                    }
                )
            )
            await db.commit()
    except Exception:
        logger.exception("observability: failed to record llm call for turn %s", turn_id)


async def record_tool_call(
    *,
    turn_id: Optional[int],
    round_index: int,
    tool_name: str,
    arguments: Optional[dict] = None,
    result: Optional[dict] = None,
    status: str = "completed",
    duration_ms: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    if not _enabled() or turn_id is None:
        return
    try:
        if arguments is not None:
            arguments = jsonable_encoder(arguments)
        if result is not None:
            result = jsonable_encoder(result)
        async with async_session() as db:
            db.add(
                ChatObsToolCall(
                    **{
                        "turn_id": turn_id,
                        "round_index": round_index,
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "result": result,
                        "status": status,
                        "duration_ms": duration_ms,
                        "error": error,
                    }
                )
            )
            await db.commit()
    except Exception:
        logger.exception("observability: failed to record tool call for turn %s", turn_id)


async def finalize_turn(
    turn_id: Optional[int],
    *,
    status: str,
    assistant_reply: Optional[str] = None,
    error: Optional[str] = None,
    duration_ms: Optional[int] = None,
    rounds_count: Optional[int] = None,
    actions_count: Optional[int] = None,
    pending_confirmation: bool = False,
    pending_question: bool = False,
    total_prompt_tokens: Optional[int] = None,
    total_completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    total_cost_usd: Optional[float] = None,
) -> None:
    if not _enabled() or turn_id is None:
        return
    values: dict[str, Any] = {
        "status": status,
        "ended_at": datetime.now(timezone.utc),
    }
    if assistant_reply is not None:
        values["assistant_reply"] = assistant_reply
    if error is not None:
        values["error"] = error
    if duration_ms is not None:
        values["duration_ms"] = duration_ms
    if rounds_count is not None:
        values["rounds_count"] = rounds_count
    if actions_count is not None:
        values["actions_count"] = actions_count
    values["pending_confirmation"] = pending_confirmation
    values["pending_question"] = pending_question
    if total_prompt_tokens is not None:
        values["total_prompt_tokens"] = total_prompt_tokens
    if total_completion_tokens is not None:
        values["total_completion_tokens"] = total_completion_tokens
    if total_tokens is not None:
        values["total_tokens"] = total_tokens
    if total_cost_usd is not None:
        values["total_cost_usd"] = total_cost_usd
    try:
        async with async_session() as db:
            await db.execute(
                update(ChatObsTurn)
                .where(ChatObsTurn.id == turn_id)
                .values(**values)
            )
            await db.commit()
    except Exception:
        logger.exception("observability: failed to finalize turn %s", turn_id)


async def _compute_cost_from_pricing(
    *, model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: Optional[int]
) -> Optional[float]:
    """Fallback cost estimate from the chat_model_pricing table."""
    if not prompt_tokens and not completion_tokens:
        return None
    try:
        async with async_session() as db:
            row = (
                await db.execute(
                    select(ChatModelPricing).where(ChatModelPricing.model == model)
                )
            ).scalars().first()
            if row is None:
                return None
            cached = cached_tokens or 0
            regular_prompt = max(prompt_tokens - cached, 0)
            cost = regular_prompt * float(row.input_price_per_1m) / 1_000_000
            if cached:
                cached_price = (
                    float(row.cached_input_price_per_1m)
                    if row.cached_input_price_per_1m is not None
                    else float(row.input_price_per_1m)
                )
                cost += cached * cached_price / 1_000_000
            cost += completion_tokens * float(row.output_price_per_1m) / 1_000_000
            return round(cost, 6)
    except Exception:
        logger.exception("observability: failed to compute cost from pricing")
        return None
