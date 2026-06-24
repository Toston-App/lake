"""CRUD for UserSubscription rows.

The Polar webhook handler is the only intended writer of these rows; reads
go through the entitlements service (which adds a Redis cache).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.core.tiers import Tier, tier_for_product
from app.models.subscription import UserSubscription


async def get_by_user(db: AsyncSession, user_id: int) -> Optional[UserSubscription]:
    result = await db.execute(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
    )
    return result.scalars().first()


async def ensure_free_row(db: AsyncSession, user_id: int) -> None:
    """Create a free-tier row if none exists. Idempotent."""
    stmt = insert(UserSubscription).values(
        user_id=user_id,
        tier=Tier.free.value,
        status="free",
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["user_id"])
    await db.execute(stmt)
    await db.commit()


def _active_tier(status: str, tier: str) -> str:
    """A subscription only grants its tier while active; otherwise free."""
    if status in ("active", "trialing"):
        return tier
    return Tier.free.value


async def upsert_from_webhook(
    db: AsyncSession,
    *,
    user_id: int,
    polar_customer_id: Optional[str],
    polar_subscription_id: Optional[str],
    product_id: Optional[str],
    status: str,
    current_period_start: Optional[datetime],
    current_period_end: Optional[datetime],
    canceled_at: Optional[datetime],
    metadata: Optional[dict[str, Any]] = None,
) -> UserSubscription:
    """Insert or update a subscription row from a Polar webhook payload.

    The granted tier is derived from `product_id` via the tier catalog; if the
    subscription is not active, the row's `tier` is kept (for history) but the
    *effective* tier (used by entitlements) falls back to free.
    """
    product_tier = tier_for_product(product_id)
    # Keep the row's `tier` set to the product's tier even on cancel so we
    # remember what they had; the entitlements service derives the effective
    # tier from `status`.
    stmt = insert(UserSubscription).values(
        user_id=user_id,
        polar_customer_id=polar_customer_id,
        polar_subscription_id=polar_subscription_id,
        tier=product_tier.value,
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        canceled_at=canceled_at,
        polar_metadata=metadata,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id"],
        set_={
            "polar_customer_id": polar_customer_id,
            "polar_subscription_id": polar_subscription_id,
            "tier": product_tier.value,
            "status": status,
            "current_period_start": current_period_start,
            "current_period_end": current_period_end,
            "canceled_at": canceled_at,
            "polar_metadata": metadata,
            "updated_at": datetime.utcnow(),
        },
    ).returning(UserSubscription)
    result = await db.execute(stmt)
    row = result.scalars().first()
    await db.commit()
    return row  # type: ignore[no-any-return]


async def set_free(
    db: AsyncSession, user_id: int, *, canceled_at: Optional[datetime] = None
) -> None:
    """Mark a user's subscription as canceled/free (keeps history)."""
    row = await get_by_user(db, user_id)
    if row is None:
        return
    row.status = "canceled" if canceled_at is not None else "free"
    row.canceled_at = canceled_at
    await db.commit()


async def set_polar_customer_id(
    db: AsyncSession, user_id: int, polar_customer_id: str
) -> None:
    """Denormalize the Polar customer id onto the User row (for checkout)."""
    from sqlalchemy import update

    from app.models.user import User
    await db.execute(
        update(User).where(User.id == user_id).values(polar_customer_id=polar_customer_id)
    )
    await db.commit()
