"""Entitlement resolution + atomic usage quota enforcement for the chat.

Before model execution, the chat endpoint calls `reserve_chat_turn`. It holds
a per-user PostgreSQL advisory lock while resolving the entitlement, counting
usage, and inserting an `in_progress` turn. Over-quota requests return 402;
reservation failures fail closed with 503 while subscriptions are enabled.

Resolution order:
1. If `SUBSCRIPTIONS_ENABLED` is False -> return the dev-default tier
   (so local dev works without Polar).
2. Otherwise read the Redis entitlement cache (`chat:ent:{user_id}`).
3. On miss, load the `UserSubscription` row; the effective tier is the
   row's lifecycle fields grant access, else `free`.
   Cache for 1h (TTL in `utilities/redis.py`).

The usage window starts at the subscription's `current_period_start` for
active subscriptions, or the first day of the current calendar month for
free users. Usage is persisted in `chat_obs_turns` even when optional detailed
chat observability is disabled. Expired reservations and server-errored turns
do not consume a message; turns the client abandons mid-stream do. Tokens a
turn actually consumed always count, whatever its status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tiers import Tier, get_tier_config, tier_from_value
from app.crud import crud_subscription
from app.models.chat_observability import ChatObsTurn
from app.models.subscription import UserSubscription
from app.models.user import User
from app.schemas.subscription import UsageOut
from app.utilities.redis import (
    get_entitlement,
    invalidate_entitlement,
    set_entitlement,
)

# Statuses that grant the subscribed tier; anything else falls back to free.
_ACTIVE_STATUSES = frozenset({"active", "trialing"})
_IMMEDIATE_LOSS_STATUSES = frozenset({"revoked", "unpaid"})
_QUOTA_LOCK_NAMESPACE = (
    0x504F4C52  # "POLR"; avoids colliding with other advisory locks.
)
# Turn whose client disconnected mid-stream. Billed like a completed turn.
TURN_STATUS_INTERRUPTED = "interrupted"


class QuotaReservationError(RuntimeError):
    """Billing usage could not be reserved safely."""


@dataclass
class QuotaResult:
    tier: Tier
    allowed: bool
    reason: str | None
    usage: UsageOut
    period_start: datetime


def _period_start_for_free() -> datetime:
    """First moment of the current calendar month (UTC)."""
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _external_id(user: User) -> str:
    """Polar `external_id`: Clerk UUID when present, else numeric User.id."""
    return user.uuid or str(user.id)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def has_paid_entitlement(
    *,
    status: str,
    cancel_at_period_end: bool,
    ends_at: datetime | None,
    ended_at: datetime | None,
    now: datetime,
    current_period_end: datetime | None = None,
) -> bool:
    """Return whether a Polar lifecycle snapshot grants paid access.

    Naive provider timestamps are interpreted as UTC. The boundary is
    exclusive: access is gone at the exact ending instant. A period that ended
    more than the renewal grace ago without a renewal webhook grants nothing.
    """
    now_utc = _as_utc(now)
    ends_at_utc = _as_utc(ends_at)
    ended_at_utc = _as_utc(ended_at)
    period_end_utc = _as_utc(current_period_end)
    if now_utc is None:  # pragma: no cover - the public contract requires now
        return False
    if status in _IMMEDIATE_LOSS_STATUSES:
        return False
    if ended_at_utc is not None and ended_at_utc <= now_utc:
        return False
    if ends_at_utc is not None and ends_at_utc <= now_utc:
        return False
    grace = timedelta(hours=settings.SUBSCRIPTION_RENEWAL_GRACE_HOURS)
    if period_end_utc is not None and period_end_utc + grace <= now_utc:
        return False
    if cancel_at_period_end:
        return bool(
            status in _ACTIVE_STATUSES | {"canceled"} and ends_at_utc is not None
        )
    if status in _ACTIVE_STATUSES:
        return True
    return False


def entitlement_payload(
    row: UserSubscription | None, *, now: datetime | None = None
) -> dict[str, str | bool | int | None]:
    tier, _ = _effective_tier_from_row(row, now=now)
    return {
        "lifecycle_version": 1,
        "tier": tier.value,
        "status": row.status if row else "free",
        "current_period_start": (
            row.current_period_start.isoformat()
            if row and row.current_period_start
            else None
        ),
        "current_period_end": (
            row.current_period_end.isoformat()
            if row and row.current_period_end
            else None
        ),
        "cancel_at_period_end": bool(row and row.cancel_at_period_end),
        "ends_at": row.ends_at.isoformat() if row and row.ends_at else None,
        "ended_at": row.ended_at.isoformat() if row and row.ended_at else None,
    }


def _parse_cached_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def effective_tier_from_payload(
    payload: dict[str, object], *, now: datetime
) -> Tier | None:
    """Re-evaluate cached lifecycle state; ``None`` means reload from DB."""
    if payload.get("lifecycle_version") != 1:
        return None
    status = payload.get("status")
    if not isinstance(status, str):
        return Tier.free
    if not has_paid_entitlement(
        status=status,
        cancel_at_period_end=payload.get("cancel_at_period_end") is True,
        ends_at=_parse_cached_dt(payload.get("ends_at")),
        ended_at=_parse_cached_dt(payload.get("ended_at")),
        now=now,
        current_period_end=_parse_cached_dt(payload.get("current_period_end")),
    ):
        return Tier.free
    try:
        return tier_from_value(str(payload.get("tier", "free")))
    except ValueError:
        return Tier.free


def _effective_tier_from_row(
    row: UserSubscription | None, *, now: datetime | None = None
) -> tuple[Tier, UserSubscription | None]:
    if row is None:
        return Tier.free, None
    if has_paid_entitlement(
        status=row.status,
        cancel_at_period_end=bool(row.cancel_at_period_end),
        ends_at=row.ends_at,
        ended_at=row.ended_at,
        now=now or datetime.now(timezone.utc),
        current_period_end=row.current_period_end,
    ):
        try:
            tier = tier_from_value(row.tier)
        except ValueError:
            tier = Tier.free
        return tier, row
    return Tier.free, row


async def get_user_tier(
    db: AsyncSession, user_id: int
) -> tuple[Tier, UserSubscription | None]:
    """Resolve the effective tier for a user (Redis-cached when enabled)."""
    if not settings.SUBSCRIPTIONS_ENABLED:
        try:
            return tier_from_value(settings.SUBSCRIPTIONS_DEV_DEFAULT_TIER), None
        except ValueError:
            return Tier.tier2, None

    cached = await get_entitlement(user_id)
    if cached:
        tier = effective_tier_from_payload(cached, now=datetime.now(timezone.utc))
        if tier is not None:
            # Callers needing period dates use `_load_row_for_period` instead.
            return tier, None
        await invalidate_entitlement(user_id)

    row = await crud_subscription.get_by_user(db, user_id)
    tier, _ = _effective_tier_from_row(row)
    await set_entitlement(user_id, entitlement_payload(row))
    return tier, row


async def _load_row_for_period(
    db: AsyncSession, user_id: int
) -> tuple[Tier, UserSubscription | None]:
    """Load the row (bypassing cache) so we can read period dates."""
    if not settings.SUBSCRIPTIONS_ENABLED:
        try:
            return tier_from_value(settings.SUBSCRIPTIONS_DEV_DEFAULT_TIER), None
        except ValueError:
            return Tier.tier2, None
    row = await crud_subscription.get_by_user(db, user_id)
    return _effective_tier_from_row(row)


async def get_period_usage(
    db: AsyncSession, user_id: int, period_start: datetime
) -> dict[str, int]:
    """Aggregate this user's chat turns since `period_start`.

    Counts messages (turns) and sums tokens. A server error doesn't burn a
    message, and neither does an in-progress reservation past its TTL. Tokens
    are summed across every turn: model usage was paid for even when the run
    failed, so errors can't be used to chat for free.
    """
    reservation_cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.CHAT_TURN_RESERVATION_TTL_SECONDS
    )
    counts_as_message = and_(
        ChatObsTurn.status != "error",
        or_(
            ChatObsTurn.status != "in_progress",
            ChatObsTurn.started_at >= reservation_cutoff,
        ),
    )
    stmt = (
        select(
            func.count(ChatObsTurn.id).filter(counts_as_message).label("messages"),
            func.coalesce(func.sum(ChatObsTurn.total_tokens), 0).label("tokens"),
        )
        .where(ChatObsTurn.user_id == user_id)
        .where(ChatObsTurn.started_at >= period_start)
    )
    row = (await db.execute(stmt)).one()
    return {
        "messages_used": int(row.messages or 0),
        "tokens_used": int(row.tokens or 0),
    }


async def check_quota(db: AsyncSession, user: User) -> QuotaResult:
    """Resolve tier + usage and decide whether a new chat turn is allowed."""
    tier, row = await _load_row_for_period(db, user.id)
    cfg = get_tier_config(tier)

    if (
        row is not None
        and has_paid_entitlement(
            status=row.status,
            cancel_at_period_end=bool(row.cancel_at_period_end),
            ends_at=row.ends_at,
            ended_at=row.ended_at,
            now=datetime.now(timezone.utc),
            current_period_end=row.current_period_end,
        )
        and row.current_period_start is not None
    ):
        period_start = row.current_period_start
        period_end = row.current_period_end
    else:
        period_start = _period_start_for_free()
        period_end = None

    usage_raw = await get_period_usage(db, user.id, period_start)
    messages_used = usage_raw["messages_used"]
    tokens_used = usage_raw["tokens_used"]

    reason: str | None = None
    allowed = True
    if (
        cfg.monthly_message_limit is not None
        and messages_used >= cfg.monthly_message_limit
    ):
        allowed = False
        reason = "message_limit_reached"
    elif cfg.monthly_token_limit is not None and tokens_used >= cfg.monthly_token_limit:
        allowed = False
        reason = "token_limit_reached"

    remaining: int | None = None
    if cfg.monthly_message_limit is not None:
        remaining = max(0, cfg.monthly_message_limit - messages_used)

    usage = UsageOut(
        period_start=period_start,
        period_end=period_end,
        messages_used=messages_used,
        messages_limit=cfg.monthly_message_limit,
        tokens_used=tokens_used,
        tokens_limit=cfg.monthly_token_limit,
        remaining=remaining,
    )
    return QuotaResult(
        tier=tier,
        allowed=allowed,
        reason=reason,
        usage=usage,
        period_start=period_start,
    )


async def reserve_chat_turn(
    db: AsyncSession,
    user: User,
    *,
    conversation_id: str,
    user_message: str,
    model: str,
    trace_id: str | None = None,
    request_id: str | None = None,
) -> tuple[QuotaResult, int | None]:
    """Atomically check quota and reserve one message slot for a chat turn."""
    try:
        await db.execute(
            select(func.pg_advisory_xact_lock(_QUOTA_LOCK_NAMESPACE, user.id))
        )
        quota = await check_quota(db, user)
        if not quota.allowed:
            await db.commit()
            return quota, None

        should_record = bool(
            settings.SUBSCRIPTIONS_ENABLED or settings.CHAT_OBSERVABILITY_ENABLED
        )
        if not should_record:
            await db.commit()
            return quota, None

        turn = ChatObsTurn(
            **{
                "conversation_id": conversation_id,
                "user_id": user.id,
                "user_message": (
                    user_message if settings.CHAT_OBSERVABILITY_ENABLED else None
                ),
                "model": model,
                "provider": "openrouter",
                "status": "in_progress",
                "trace_id": trace_id,
                "request_id": request_id,
            }
        )
        db.add(turn)
        await db.flush()
        turn_id = int(turn.id)
        await db.commit()
        return quota, turn_id
    except Exception as exc:
        await db.rollback()
        if settings.SUBSCRIPTIONS_ENABLED:
            raise QuotaReservationError("quota reservation failed") from exc
        quota = await check_quota(db, user)
        return quota, None


async def invalidate(user_id: int) -> None:
    """Drop the cached entitlement (called after a webhook update)."""
    await invalidate_entitlement(user_id)


def external_id(user: User) -> str:
    """Public accessor for the Polar `external_id` rule."""
    return _external_id(user)
