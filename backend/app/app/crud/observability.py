"""Aggregation queries for the chat observability dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_observability import (
    ChatObsLLMCall,
    ChatObsToolCall,
    ChatObsTurn,
)
from app.models.user import User


async def get_summary(
    db: AsyncSession, *, start: Optional[datetime] = None, end: Optional[datetime] = None
) -> dict[str, Any]:
    stmt = (
        select(
            func.count(ChatObsTurn.id).label("turns"),
            func.count(ChatObsTurn.id)
            .filter(ChatObsTurn.status.in_(("error", "max_rounds")))
            .label("errors"),
            func.coalesce(func.sum(ChatObsTurn.total_prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(ChatObsTurn.total_completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(ChatObsTurn.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(ChatObsTurn.total_cost_usd), 0).label("total_cost"),
            func.coalesce(func.avg(ChatObsTurn.total_cost_usd), 0).label("avg_cost_per_turn"),
            func.coalesce(func.avg(ChatObsTurn.duration_ms), 0).label("avg_duration_ms"),
            func.count(ChatObsTurn.id)
            .filter(ChatObsTurn.pending_confirmation.is_(True))
            .label("pending_confirmations"),
            func.count(ChatObsTurn.id)
            .filter(ChatObsTurn.pending_question.is_(True))
            .label("pending_questions"),
            func.count(User.id.distinct()).label("unique_users"),
            func.count(ChatObsTurn.conversation_id.distinct()).label("unique_conversations"),
        )
        .select_from(ChatObsTurn)
        .outerjoin(User, User.id == ChatObsTurn.user_id)
    )
    stmt = _apply_time_window(stmt, ChatObsTurn.started_at, start, end)
    row = (await db.execute(stmt)).one()
    mapping = row._mapping
    turns = int(mapping["turns"] or 0)
    errors = int(mapping["errors"] or 0)
    return {
        "turns": turns,
        "errors": errors,
        "error_rate": (errors / turns) if turns else 0.0,
        "prompt_tokens": int(mapping["prompt_tokens"] or 0),
        "completion_tokens": int(mapping["completion_tokens"] or 0),
        "total_tokens": int(mapping["total_tokens"] or 0),
        "total_cost": _to_float(mapping["total_cost"]),
        "avg_cost_per_turn": _to_float(mapping["avg_cost_per_turn"]),
        "avg_duration_ms": _to_float(mapping["avg_duration_ms"]),
        "pending_confirmations": int(mapping["pending_confirmations"] or 0),
        "pending_questions": int(mapping["pending_questions"] or 0),
        "unique_users": int(mapping["unique_users"] or 0),
        "unique_conversations": int(mapping["unique_conversations"] or 0),
    }


async def get_cost_timeseries(
    db: AsyncSession,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    bucket: str = "day",
) -> list[dict[str, Any]]:
    # Validate bucket to a safe literal set (also enforced at the API layer).
    if bucket not in ("hour", "day", "week", "month"):
        bucket = "day"
    bucket_expr = func.date_trunc(bucket, ChatObsTurn.started_at)
    stmt = (
        select(
            bucket_expr.label("bucket"),
            func.count(ChatObsTurn.id).label("turns"),
            func.coalesce(func.sum(ChatObsTurn.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(ChatObsTurn.total_cost_usd), 0).label("cost"),
        )
        .group_by("bucket")
        .order_by(text("bucket"))
    )
    stmt = _apply_time_window(stmt, ChatObsTurn.started_at, start, end)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "bucket": r._mapping["bucket"].isoformat() if r._mapping["bucket"] else None,
            "turns": int(r._mapping["turns"] or 0),
            "tokens": int(r._mapping["tokens"] or 0),
            "cost": _to_float(r._mapping["cost"]),
        }
        for r in rows
    ]


async def get_tool_stats(
    db: AsyncSession, *, start: Optional[datetime] = None, end: Optional[datetime] = None
) -> list[dict[str, Any]]:
    stmt = (
        select(
            ChatObsToolCall.tool_name.label("tool_name"),
            func.count(ChatObsToolCall.id).label("calls"),
            func.count(ChatObsToolCall.id)
            .filter(ChatObsToolCall.status == "failed")
            .label("failed"),
            func.coalesce(func.avg(ChatObsToolCall.duration_ms), 0).label("avg_duration_ms"),
        )
        .group_by(ChatObsToolCall.tool_name)
        .order_by(func.count(ChatObsToolCall.id).desc())
    )
    stmt = _apply_time_window(stmt, ChatObsToolCall.created_at, start, end)
    rows = (await db.execute(stmt)).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        calls = int(r._mapping["calls"] or 0)
        failed = int(r._mapping["failed"] or 0)
        out.append(
            {
                "tool_name": r._mapping["tool_name"],
                "calls": calls,
                "failed": failed,
                "error_rate": (failed / calls) if calls else 0.0,
                "avg_duration_ms": _to_float(r._mapping["avg_duration_ms"]),
            }
        )
    return out


async def get_model_stats(
    db: AsyncSession, *, start: Optional[datetime] = None, end: Optional[datetime] = None
) -> list[dict[str, Any]]:
    stmt = (
        select(
            ChatObsLLMCall.model.label("model"),
            func.count(ChatObsLLMCall.id).label("calls"),
            func.coalesce(func.sum(ChatObsLLMCall.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(ChatObsLLMCall.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(ChatObsLLMCall.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(ChatObsLLMCall.cost_usd), 0).label("cost"),
            func.coalesce(func.avg(ChatObsLLMCall.duration_ms), 0).label("avg_duration_ms"),
        )
        .group_by(ChatObsLLMCall.model)
        .order_by(func.coalesce(func.sum(ChatObsLLMCall.cost_usd), 0).desc())
    )
    stmt = _apply_time_window(stmt, ChatObsLLMCall.created_at, start, end)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "model": r._mapping["model"],
            "calls": int(r._mapping["calls"] or 0),
            "prompt_tokens": int(r._mapping["prompt_tokens"] or 0),
            "completion_tokens": int(r._mapping["completion_tokens"] or 0),
            "total_tokens": int(r._mapping["total_tokens"] or 0),
            "cost": _to_float(r._mapping["cost"]),
            "avg_duration_ms": _to_float(r._mapping["avg_duration_ms"]),
        }
        for r in rows
    ]


async def get_turns(
    db: AsyncSession,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
    conversation_id: Optional[str] = None,
    model: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            ChatObsTurn.id,
            ChatObsTurn.conversation_id,
            ChatObsTurn.user_id,
            User.email.label("user_email"),
            ChatObsTurn.model,
            ChatObsTurn.status,
            ChatObsTurn.duration_ms,
            ChatObsTurn.rounds_count,
            ChatObsTurn.actions_count,
            ChatObsTurn.total_tokens,
            ChatObsTurn.total_cost_usd,
            ChatObsTurn.pending_confirmation,
            ChatObsTurn.pending_question,
            ChatObsTurn.error,
            ChatObsTurn.user_message,
            ChatObsTurn.started_at,
        )
        .select_from(ChatObsTurn)
        .outerjoin(User, User.id == ChatObsTurn.user_id)
        .order_by(ChatObsTurn.started_at.desc())
    )
    if status:
        stmt = stmt.where(ChatObsTurn.status == status)
    if user_id is not None:
        stmt = stmt.where(ChatObsTurn.user_id == user_id)
    if conversation_id:
        stmt = stmt.where(ChatObsTurn.conversation_id == conversation_id)
    if model:
        stmt = stmt.where(ChatObsTurn.model == model)
    if search:
        stmt = stmt.where(ChatObsTurn.user_message.ilike(f"%{search}%"))
    stmt = _apply_time_window(stmt, ChatObsTurn.started_at, start, end)
    stmt = stmt.offset(skip).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "conversation_id": r.conversation_id,
            "user_id": r.user_id,
            "user_email": r.user_email,
            "model": r.model,
            "status": r.status,
            "duration_ms": r.duration_ms,
            "rounds_count": r.rounds_count,
            "actions_count": r.actions_count,
            "total_tokens": r.total_tokens,
            "total_cost_usd": _to_float(r.total_cost_usd),
            "pending_confirmation": r.pending_confirmation,
            "pending_question": r.pending_question,
            "error": r.error,
            "user_message": r.user_message,
            "started_at": r.started_at.isoformat() if r.started_at else None,
        }
        for r in rows
    ]


async def get_turn_detail(db: AsyncSession, turn_id: int) -> Optional[dict[str, Any]]:
    turn_row = (
        await db.execute(
            select(ChatObsTurn, User.email.label("user_email"))
            .select_from(ChatObsTurn)
            .outerjoin(User, User.id == ChatObsTurn.user_id)
            .where(ChatObsTurn.id == turn_id)
        )
    ).one_or_none()
    if turn_row is None:
        return None
    turn = turn_row[0]
    user_email = turn_row[1]

    llm_rows = (
        await db.execute(
            select(ChatObsLLMCall)
            .where(ChatObsLLMCall.turn_id == turn_id)
            .order_by(ChatObsLLMCall.round_index)
        )
    ).scalars().all()
    tool_rows = (
        await db.execute(
            select(ChatObsToolCall)
            .where(ChatObsToolCall.turn_id == turn_id)
            .order_by(ChatObsToolCall.id)
        )
    ).scalars().all()

    return {
        "id": turn.id,
        "conversation_id": turn.conversation_id,
        "user_id": turn.user_id,
        "user_email": user_email,
        "trace_id": turn.trace_id,
        "request_id": turn.request_id,
        "model": turn.model,
        "provider": turn.provider,
        "user_message": turn.user_message,
        "assistant_reply": turn.assistant_reply,
        "status": turn.status,
        "error": turn.error,
        "duration_ms": turn.duration_ms,
        "rounds_count": turn.rounds_count,
        "actions_count": turn.actions_count,
        "pending_confirmation": turn.pending_confirmation,
        "pending_question": turn.pending_question,
        "total_prompt_tokens": turn.total_prompt_tokens,
        "total_completion_tokens": turn.total_completion_tokens,
        "total_tokens": turn.total_tokens,
        "total_cost_usd": _to_float(turn.total_cost_usd),
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "ended_at": turn.ended_at.isoformat() if turn.ended_at else None,
        "llm_calls": [
            {
                "id": c.id,
                "round_index": c.round_index,
                "model": c.model,
                "prompt_tokens": c.prompt_tokens,
                "completion_tokens": c.completion_tokens,
                "total_tokens": c.total_tokens,
                "cached_tokens": c.cached_tokens,
                "cost_usd": _to_float(c.cost_usd),
                "duration_ms": c.duration_ms,
                "finish_reason": c.finish_reason,
                "had_tool_calls": c.had_tool_calls,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in llm_rows
        ],
        "tool_calls": [
            {
                "id": c.id,
                "round_index": c.round_index,
                "tool_name": c.tool_name,
                "arguments": c.arguments,
                "result": c.result,
                "status": c.status,
                "duration_ms": c.duration_ms,
                "error": c.error,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in tool_rows
        ],
    }


def _apply_time_window(
    stmt: Any, col: Any, start: Optional[datetime], end: Optional[datetime]
) -> Any:
    if start is not None:
        stmt = stmt.where(col >= start)
    if end is not None:
        stmt = stmt.where(col < end)
    return stmt


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
