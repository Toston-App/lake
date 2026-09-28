"""CRUD for UserSubscription rows.

The Polar webhook handler is the only intended writer of these rows; reads
go through the entitlements service (which adds a Redis cache).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import and_, func, or_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.core.tiers import Tier, tier_for_product
from app.models.polar_webhook_event import PolarWebhookEvent
from app.models.subscription import UserSubscription

_SUBSCRIPTION_LOCK_NAMESPACE = 0x53554253  # "SUBS"


async def get_by_user(db: AsyncSession, user_id: int) -> UserSubscription | None:
    result = await db.execute(
        select(UserSubscription)
        .where(UserSubscription.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return cast(UserSubscription | None, result.scalars().first())


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


async def claim_webhook(
    db: AsyncSession, webhook_id: str, event_type: str | None = None
) -> bool:
    """Claim a delivery in the caller's transaction.

    The insert and all webhook state changes must be committed together. A
    rollback removes the claim so Polar can safely retry the same delivery.
    """
    stmt = (
        insert(PolarWebhookEvent)
        .values(webhook_id=webhook_id, event_type=event_type)
        .on_conflict_do_nothing(index_elements=["webhook_id"])
        .returning(PolarWebhookEvent.webhook_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def upsert_from_webhook(
    db: AsyncSession,
    *,
    user_id: int,
    polar_customer_id: str | None,
    polar_subscription_id: str | None,
    product_id: str | None,
    status: str,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
    canceled_at: datetime | None,
    cancel_at_period_end: bool = False,
    ends_at: datetime | None = None,
    ended_at: datetime | None = None,
    pending_tier: str | None = None,
    pending_effective_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
    event_at: datetime | None = None,
    event_priority: int = 0,
) -> UserSubscription | None:
    """Insert or update a subscription row from a Polar webhook payload.

    Returns None when a newer event was already applied (out-of-order delivery).
    The granted tier is derived from `product_id` via the tier catalog; if the
    subscription is not active, the row's `tier` is kept (for history) but the
    *effective* tier (used by entitlements) falls back to free.
    """
    # Separate event types have different webhook ids and can arrive at the
    # same time. Serialize mutations for one local user so concurrent first
    # deliveries cannot race across the user/customer unique indexes.
    await db.execute(
        select(func.pg_advisory_xact_lock(_SUBSCRIPTION_LOCK_NAMESPACE, user_id))
    )

    product_tier = tier_for_product(product_id)
    if event_at is not None and event_at.tzinfo is None:
        event_at = event_at.replace(tzinfo=timezone.utc)

    stmt = insert(UserSubscription).values(
        user_id=user_id,
        polar_customer_id=polar_customer_id,
        polar_subscription_id=polar_subscription_id,
        tier=product_tier.value,
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        canceled_at=canceled_at,
        cancel_at_period_end=cancel_at_period_end,
        ends_at=ends_at,
        ended_at=ended_at,
        pending_tier=pending_tier,
        pending_effective_at=pending_effective_at,
        last_event_at=event_at,
        last_event_priority=event_priority,
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
            "cancel_at_period_end": cancel_at_period_end,
            "ends_at": ends_at,
            "ended_at": ended_at,
            "pending_tier": pending_tier,
            "pending_effective_at": pending_effective_at,
            "last_event_at": event_at,
            "last_event_priority": event_priority,
            "polar_metadata": metadata,
            "updated_at": datetime.now(timezone.utc),
        },
        where=or_(
            UserSubscription.last_event_at.is_(None),
            stmt.excluded.last_event_at > UserSubscription.last_event_at,
            (
                (stmt.excluded.last_event_at == UserSubscription.last_event_at)
                & (
                    stmt.excluded.last_event_priority
                    > UserSubscription.last_event_priority
                )
            ),
        ),
    ).returning(UserSubscription.id)
    result = await db.execute(stmt)
    applied_id = result.scalar_one_or_none()
    if applied_id is None:
        return None
    return await get_by_user(db, user_id)


async def apply_lifecycle_from_webhook(
    db: AsyncSession,
    *,
    user_id: int,
    polar_subscription_id: str,
    status: str,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
    canceled_at: datetime | None,
    cancel_at_period_end: bool,
    ends_at: datetime | None,
    ended_at: datetime | None,
    metadata: dict[str, Any] | None,
    event_at: datetime,
    event_priority: int,
) -> UserSubscription | None:
    """Apply only lifecycle fields from a snapshot whose product is unmapped.

    The row keeps its tier, and only a row already tracking this subscription
    is touched, so an unrelated subscription can't revoke or grant anything.
    Returns None when no row matched or a newer event was already applied.
    """
    await db.execute(
        select(func.pg_advisory_xact_lock(_SUBSCRIPTION_LOCK_NAMESPACE, user_id))
    )
    if event_at.tzinfo is None:
        event_at = event_at.replace(tzinfo=timezone.utc)
    stmt = (
        update(UserSubscription)
        .where(UserSubscription.user_id == user_id)
        .where(UserSubscription.polar_subscription_id == polar_subscription_id)
        .where(
            or_(
                UserSubscription.last_event_at.is_(None),
                UserSubscription.last_event_at < event_at,
                and_(
                    UserSubscription.last_event_at == event_at,
                    UserSubscription.last_event_priority < event_priority,
                ),
            )
        )
        .values(
            status=status,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
            canceled_at=canceled_at,
            cancel_at_period_end=cancel_at_period_end,
            ends_at=ends_at,
            ended_at=ended_at,
            last_event_at=event_at,
            last_event_priority=event_priority,
            polar_metadata=metadata,
            updated_at=datetime.now(timezone.utc),
        )
        .returning(UserSubscription.id)
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        return None
    return await get_by_user(db, user_id)


async def set_free(
    db: AsyncSession, user_id: int, *, canceled_at: datetime | None = None
) -> None:
    """Mark a user's subscription as canceled/free (keeps history)."""
    row = await get_by_user(db, user_id)
    if row is None:
        return
    row.status = "canceled" if canceled_at is not None else "free"
    row.canceled_at = canceled_at
    await db.flush()


async def set_polar_customer_id(
    db: AsyncSession, user_id: int, polar_customer_id: str
) -> None:
    """Denormalize the Polar customer id onto the User row (for checkout).

    Refuses to bind a customer id already owned by a different user.
    """
    from app.models.user import User

    existing_user = (
        (
            await db.execute(
                select(User).where(User.polar_customer_id == polar_customer_id)
            )
        )
        .scalars()
        .first()
    )
    if existing_user is not None and existing_user.id != user_id:
        raise ValueError(
            f"polar_customer_id {polar_customer_id!r} already bound to user {existing_user.id}"
        )

    existing_sub = (
        (
            await db.execute(
                select(UserSubscription).where(
                    UserSubscription.polar_customer_id == polar_customer_id
                )
            )
        )
        .scalars()
        .first()
    )
    if existing_sub is not None and existing_sub.user_id != user_id:
        raise ValueError(
            f"polar_customer_id {polar_customer_id!r} already bound to user {existing_sub.user_id}"
        )

    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(polar_customer_id=polar_customer_id)
    )
    await db.execute(
        update(UserSubscription)
        .where(UserSubscription.user_id == user_id)
        .values(polar_customer_id=polar_customer_id)
    )
    await db.flush()
