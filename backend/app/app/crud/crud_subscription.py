"""CRUD for UserSubscription rows.

The Polar webhook handler is the only intended writer of these rows; reads
go through the entitlements service (which adds a Redis cache).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.core.tiers import Tier, tier_for_product
from app.models.polar_webhook_event import PolarWebhookEvent
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


async def is_webhook_processed(db: AsyncSession, webhook_id: str) -> bool:
    result = await db.execute(
        select(PolarWebhookEvent.webhook_id).where(
            PolarWebhookEvent.webhook_id == webhook_id
        )
    )
    return result.scalars().first() is not None


async def mark_webhook_processed(
    db: AsyncSession, webhook_id: str, event_type: Optional[str] = None
) -> None:
    """Record a Standard Webhooks delivery id after successful handling."""
    stmt = (
        insert(PolarWebhookEvent)
        .values(webhook_id=webhook_id, event_type=event_type)
        .on_conflict_do_nothing(index_elements=["webhook_id"])
    )
    await db.execute(stmt)
    await db.commit()


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
    event_at: Optional[datetime] = None,
) -> Optional[UserSubscription]:
    """Insert or update a subscription row from a Polar webhook payload.

    Returns None when a newer event was already applied (out-of-order delivery).
    The granted tier is derived from `product_id` via the tier catalog; if the
    subscription is not active, the row's `tier` is kept (for history) but the
    *effective* tier (used by entitlements) falls back to free.
    """
    product_tier = tier_for_product(product_id)
    if event_at is not None and event_at.tzinfo is None:
        event_at = event_at.replace(tzinfo=timezone.utc)

    existing = await get_by_user(db, user_id)
    if (
        existing is not None
        and event_at is not None
        and existing.last_event_at is not None
        and event_at < existing.last_event_at
    ):
        return None

    stmt = insert(UserSubscription).values(
        user_id=user_id,
        polar_customer_id=polar_customer_id,
        polar_subscription_id=polar_subscription_id,
        tier=product_tier.value,
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        canceled_at=canceled_at,
        last_event_at=event_at,
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
            "last_event_at": event_at,
            "polar_metadata": metadata,
            "updated_at": datetime.now(timezone.utc),
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
    """Denormalize the Polar customer id onto the User row (for checkout).

    Refuses to bind a customer id already owned by a different user.
    """
    from sqlalchemy import update

    from app.models.user import User

    existing_user = (
        await db.execute(
            select(User).where(User.polar_customer_id == polar_customer_id)
        )
    ).scalars().first()
    if existing_user is not None and existing_user.id != user_id:
        raise ValueError(
            f"polar_customer_id {polar_customer_id!r} already bound to user {existing_user.id}"
        )

    existing_sub = (
        await db.execute(
            select(UserSubscription).where(
                UserSubscription.polar_customer_id == polar_customer_id
            )
        )
    ).scalars().first()
    if existing_sub is not None and existing_sub.user_id != user_id:
        raise ValueError(
            f"polar_customer_id {polar_customer_id!r} already bound to user {existing_sub.user_id}"
        )

    await db.execute(
        update(User).where(User.id == user_id).values(polar_customer_id=polar_customer_id)
    )
    await db.execute(
        update(UserSubscription)
        .where(UserSubscription.user_id == user_id)
        .values(polar_customer_id=polar_customer_id)
    )
    await db.commit()
