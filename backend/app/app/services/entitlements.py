"""Entitlement resolution + usage quota enforcement for the chat.

The chat endpoints call `check_quota(db, user)` before each turn. The result
carries the effective tier (used to gate tool definitions) and a usage
snapshot. When the user is over their message or token cap, `allowed` is
False and the chat endpoint returns 402.

Resolution order:
1. If `SUBSCRIPTIONS_ENABLED` is False -> return the dev-default tier
   (so local dev works without Polar).
2. Otherwise read the Redis entitlement cache (`chat:ent:{user_id}`).
3. On miss, load the `UserSubscription` row; the effective tier is the
   row's `tier` only while `status` is active/trialing, else `free`.
   Cache for 1h (TTL in `utilities/redis.py`).

The usage window starts at the subscription's `current_period_start` for
active subscriptions, or the first day of the current calendar month for
free users. Usage is aggregated from `chat_obs_turns` (counts messages and
sums `total_tokens`), reusing the existing observability telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
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


@dataclass
class QuotaResult:
    tier: Tier
    allowed: bool
    reason: Optional[str]
    usage: UsageOut
    period_start: datetime


def _period_start_for_free() -> datetime:
    """First moment of the current calendar month (UTC)."""
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _external_id(user: User) -> str:
    """Polar `external_id`: Clerk UUID when present, else numeric User.id."""
    return user.uuid or str(user.id)


def _effective_tier_from_row(row: Optional[UserSubscription]) -> tuple[Tier, UserSubscription]:
    if row is None:
        return Tier.free, None  # type: ignore[return-value]
    if row.status in _ACTIVE_STATUSES:
        try:
            tier = tier_from_value(row.tier)
        except ValueError:
            tier = Tier.free
        return tier, row
    return Tier.free, row


async def get_user_tier(
    db: AsyncSession, user_id: int
) -> tuple[Tier, Optional[UserSubscription]]:
    """Resolve the effective tier for a user (Redis-cached when enabled)."""
    if not settings.SUBSCRIPTIONS_ENABLED:
        try:
            return tier_from_value(settings.SUBSCRIPTIONS_DEV_DEFAULT_TIER), None
        except ValueError:
            return Tier.tier3, None

    cached = await get_entitlement(user_id)
    if cached:
        try:
            tier = tier_from_value(cached.get("tier", "free"))
        except ValueError:
            tier = Tier.free
        # Reconstruct a lightweight row-like dict; callers that need the row
        # (period dates) should use `_load_and_cache` instead.
        return tier, None

    row = await crud_subscription.get_by_user(db, user_id)
    tier, _ = _effective_tier_from_row(row)
    await set_entitlement(
        user_id,
        {
            "tier": tier.value,
            "status": row.status if row else "free",
            "current_period_start": (
                row.current_period_start.isoformat() if row and row.current_period_start else None
            ),
            "current_period_end": (
                row.current_period_end.isoformat() if row and row.current_period_end else None
            ),
        },
    )
    return tier, row


async def _load_row_for_period(
    db: AsyncSession, user_id: int
) -> tuple[Tier, Optional[UserSubscription]]:
    """Load the row (bypassing cache) so we can read period dates."""
    if not settings.SUBSCRIPTIONS_ENABLED:
        try:
            return tier_from_value(settings.SUBSCRIPTIONS_DEV_DEFAULT_TIER), None
        except ValueError:
            return Tier.tier3, None
    row = await crud_subscription.get_by_user(db, user_id)
    return _effective_tier_from_row(row)


async def get_period_usage(
    db: AsyncSession, user_id: int, period_start: datetime
) -> dict[str, int]:
    """Aggregate this user's chat turns since `period_start`.

    Counts messages (turns) and sums tokens. Excludes errored turns so a
    transient server error doesn't burn a user's quota.
    """
    stmt = (
        select(
            func.count(ChatObsTurn.id).label("messages"),
            func.coalesce(func.sum(ChatObsTurn.total_tokens), 0).label("tokens"),
        )
        .where(ChatObsTurn.user_id == user_id)
        .where(ChatObsTurn.started_at >= period_start)
        .where(ChatObsTurn.status != "error")
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
        and row.status in _ACTIVE_STATUSES
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

    reason: Optional[str] = None
    allowed = True
    if cfg.monthly_message_limit is not None and messages_used >= cfg.monthly_message_limit:
        allowed = False
        reason = "message_limit_reached"
    elif cfg.monthly_token_limit is not None and tokens_used >= cfg.monthly_token_limit:
        allowed = False
        reason = "token_limit_reached"

    remaining: Optional[int] = None
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
    return QuotaResult(tier=tier, allowed=allowed, reason=reason, usage=usage, period_start=period_start)


async def invalidate(user_id: int) -> None:
    """Drop the cached entitlement (called after a webhook update)."""
    await invalidate_entitlement(user_id)


def external_id(user: User) -> str:
    """Public accessor for the Polar `external_id` rule."""
    return _external_id(user)
