"""Aggregation queries for the chat observability dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tiers import Tier, get_tier_config, tier_from_value
from app.models.chat_observability import (
    ChatObsLLMCall,
    ChatObsToolCall,
    ChatObsTurn,
)
from app.models.subscription import UserSubscription
from app.models.user import User
from app.services.entitlements import has_paid_entitlement


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


def _window_days(start: datetime | None, end: datetime | None) -> int:
    now = datetime.now(timezone.utc)
    end_dt = end or now
    start_dt = start or (end_dt - timedelta(days=30))
    seconds = (end_dt - start_dt).total_seconds()
    return max(1, int(seconds // 86400))


def _money(v: float) -> float:
    return round(v, 6)


def _build_economics_row(
    *,
    user_id: int,
    email: str | None,
    name: str | None,
    subscription: UserSubscription | None,
    turns: int,
    tokens: int,
    ai_cost_usd: float,
    window_days: int,
    now: datetime,
) -> dict[str, Any] | None:
    status = subscription.status if subscription is not None else "free"
    is_paid = False
    effective = Tier.free
    if subscription is not None and has_paid_entitlement(
        status=subscription.status,
        cancel_at_period_end=bool(subscription.cancel_at_period_end),
        ends_at=subscription.ends_at,
        ended_at=subscription.ended_at,
        now=now,
    ):
        try:
            effective = tier_from_value(subscription.tier)
        except ValueError:
            effective = Tier.free
        else:
            is_paid = effective != Tier.free

    if turns <= 0 and not is_paid:
        return None

    cfg = get_tier_config(effective)
    monthly = cfg.monthly_price_usd if is_paid else 0.0
    revenue = monthly * (window_days / 30.0) if is_paid else 0.0
    cost = ai_cost_usd or 0.0
    return {
        "user_id": user_id,
        "email": email,
        "name": name,
        "tier": effective.value,
        "tier_label": cfg.label,
        "status": status,
        "is_paid": is_paid,
        "turns": turns,
        "tokens": tokens,
        "messages_limit": cfg.monthly_message_limit,
        "tokens_limit": cfg.monthly_token_limit,
        "ai_cost_usd": _money(cost),
        "monthly_price_usd": _money(monthly),
        "estimated_revenue_usd": _money(revenue),
        "profit_usd": _money(revenue - cost),
    }


async def _load_economics_rows(
    db: AsyncSession,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    search: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    window_days = _window_days(start, end)
    usage_stmt = select(
        ChatObsTurn.user_id.label("user_id"),
        func.count(ChatObsTurn.id).label("turns"),
        func.coalesce(func.sum(ChatObsTurn.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(ChatObsTurn.total_cost_usd), 0).label("ai_cost_usd"),
    ).group_by(ChatObsTurn.user_id)
    usage_stmt = _apply_time_window(usage_stmt, ChatObsTurn.started_at, start, end)
    usage_sub = usage_stmt.subquery()

    stmt = (
        select(
            User,
            UserSubscription,
            func.coalesce(usage_sub.c.turns, 0).label("turns"),
            func.coalesce(usage_sub.c.tokens, 0).label("tokens"),
            func.coalesce(usage_sub.c.ai_cost_usd, 0).label("ai_cost_usd"),
        )
        .select_from(User)
        .outerjoin(UserSubscription, UserSubscription.user_id == User.id)
        .outerjoin(usage_sub, usage_sub.c.user_id == User.id)
        .where(
            or_(
                usage_sub.c.user_id.is_not(None),
                UserSubscription.id.is_not(None),
            )
        )
    )
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(pattern), User.name.ilike(pattern)))

    now = datetime.now(timezone.utc)
    rows = (await db.execute(stmt)).all()
    out: list[dict[str, Any]] = []
    for user, subscription, turns, tokens, ai_cost_usd in rows:
        built = _build_economics_row(
            user_id=user.id,
            email=user.email,
            name=user.name,
            subscription=subscription,
            turns=int(turns or 0),
            tokens=int(tokens or 0),
            ai_cost_usd=_to_float(ai_cost_usd) or 0.0,
            window_days=window_days,
            now=now,
        )
        if built is not None:
            out.append(built)
    return window_days, out


async def get_economics_summary(
    db: AsyncSession,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    window_days, rows = await _load_economics_rows(db, start=start, end=end)
    by_tier: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = by_tier.setdefault(
            row["tier"],
            {
                "tier": row["tier"],
                "label": row["tier_label"],
                "users": 0,
                "ai_cost_usd": 0.0,
                "estimated_revenue_usd": 0.0,
                "profit_usd": 0.0,
            },
        )
        bucket["users"] += 1
        bucket["ai_cost_usd"] += row["ai_cost_usd"]
        bucket["estimated_revenue_usd"] += row["estimated_revenue_usd"]
        bucket["profit_usd"] += row["profit_usd"]

    total_cost = sum(r["ai_cost_usd"] for r in rows)
    total_revenue = sum(r["estimated_revenue_usd"] for r in rows)
    return {
        "window_days": window_days,
        "users": len(rows),
        "paying_users": sum(1 for r in rows if r["is_paid"]),
        "losing_users": sum(1 for r in rows if r["profit_usd"] < 0),
        "winning_users": sum(1 for r in rows if r["profit_usd"] >= 0),
        "ai_cost_usd": _money(total_cost),
        "estimated_revenue_usd": _money(total_revenue),
        "profit_usd": _money(total_revenue - total_cost),
        "by_tier": [
            {
                "tier": b["tier"],
                "label": b["label"],
                "users": b["users"],
                "ai_cost_usd": _money(b["ai_cost_usd"]),
                "estimated_revenue_usd": _money(b["estimated_revenue_usd"]),
                "profit_usd": _money(b["profit_usd"]),
            }
            for b in sorted(by_tier.values(), key=lambda x: x["tier"])
        ],
    }


_SORT_KEYS = {
    "profit": "profit_usd",
    "cost": "ai_cost_usd",
    "revenue": "estimated_revenue_usd",
    "tokens": "tokens",
    "turns": "turns",
}


async def get_economics_users(
    db: AsyncSession,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    tier: str | None = None,
    outcome: Literal["winning", "losing", "all"] = "all",
    search: str | None = None,
    sort: Literal["profit", "cost", "revenue", "tokens", "turns"] = "profit",
    order: Literal["asc", "desc"] = "asc",
    skip: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    _, rows = await _load_economics_rows(db, start=start, end=end, search=search)
    if tier:
        rows = [r for r in rows if r["tier"] == tier]
    if outcome == "winning":
        rows = [r for r in rows if r["profit_usd"] >= 0]
    elif outcome == "losing":
        rows = [r for r in rows if r["profit_usd"] < 0]

    key = _SORT_KEYS.get(sort, "profit_usd")
    rows.sort(key=lambda r: r[key], reverse=(order == "desc"))
    return {"items": rows[skip : skip + limit], "count": len(rows)}

