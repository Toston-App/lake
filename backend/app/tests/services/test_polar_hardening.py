import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.config import settings
from app.core.tiers import Tier, configure_product_ids
from app.crud import crud_subscription
from app.models.subscription import UserSubscription
from app.models.user import User
from app.services import polar as polar_service
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TestSessionLocal


def _subscription(user_id: int, *, tier: str = "tier1") -> UserSubscription:
    return UserSubscription(
        user_id=user_id,
        polar_customer_id="customer_test",
        polar_subscription_id="subscription_test",
        tier=tier,
        status="active",
        current_period_start=datetime.now(timezone.utc) - timedelta(days=1),
    )


async def test_webhook_claim_is_atomic_and_rollback_releases_it(
    db_session: AsyncSession,
) -> None:
    assert await crud_subscription.claim_webhook(db_session, "delivery_test")
    assert not await crud_subscription.claim_webhook(db_session, "delivery_test")
    await db_session.rollback()
    assert await crud_subscription.claim_webhook(db_session, "delivery_test")


async def test_older_and_equal_webhook_snapshots_cannot_overwrite_newer_state(
    db_session: AsyncSession, test_user: User
) -> None:
    configure_product_ids({"tier1": "product_1", "tier2": "product_2"})
    db_session.add(_subscription(test_user.id))
    await db_session.commit()

    newer_at = datetime.now(timezone.utc)
    newer = await crud_subscription.upsert_from_webhook(
        db_session,
        user_id=test_user.id,
        polar_customer_id="customer_test",
        polar_subscription_id="subscription_test",
        product_id="product_2",
        status="active",
        current_period_start=newer_at,
        current_period_end=None,
        canceled_at=None,
        event_at=newer_at,
    )
    await db_session.commit()
    assert newer is not None
    assert newer.tier == "tier2"

    for stale_at in (newer_at, newer_at - timedelta(seconds=1)):
        stale = await crud_subscription.upsert_from_webhook(
            db_session,
            user_id=test_user.id,
            polar_customer_id="customer_test",
            polar_subscription_id="subscription_test",
            product_id="product_1",
            status="canceled",
            current_period_start=stale_at,
            current_period_end=None,
            canceled_at=stale_at,
            event_at=stale_at,
        )
        assert stale is None

    row = await crud_subscription.get_by_user(db_session, test_user.id)
    assert row is not None
    assert row.tier == "tier2"
    assert row.status == "active"


async def test_concurrent_first_webhooks_serialize_without_unique_violation() -> None:
    configure_product_ids({"tier1": "product_1", "tier3": "product_3"})
    email = f"polar-race-{uuid4()}@example.com"
    customer_id = f"customer-{uuid4()}"

    async with TestSessionLocal() as setup:
        user = User(email=email, country="USD", is_active=True, is_superuser=False)
        setup.add(user)
        await setup.commit()
        user_id = user.id

    older_at = datetime.now(timezone.utc)
    newer_at = older_at + timedelta(milliseconds=1)

    async def apply(product_id: str, event_at: datetime) -> None:
        async with TestSessionLocal() as session:
            await crud_subscription.upsert_from_webhook(
                session,
                user_id=user_id,
                polar_customer_id=customer_id,
                polar_subscription_id="subscription-race",
                product_id=product_id,
                status="active",
                current_period_start=older_at,
                current_period_end=None,
                canceled_at=None,
                event_at=event_at,
            )
            await session.commit()

    try:
        await asyncio.gather(
            apply("product_1", older_at),
            apply("product_3", newer_at),
        )
        async with TestSessionLocal() as verify:
            row = await crud_subscription.get_by_user(verify, user_id)
            assert row is not None
            assert row.polar_customer_id == customer_id
            assert row.tier == "tier3"
            assert row.last_event_at == newer_at
    finally:
        async with TestSessionLocal() as cleanup:
            await cleanup.execute(
                delete(UserSubscription).where(UserSubscription.user_id == user_id)
            )
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()


async def test_transient_customer_lookup_is_retryable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = SimpleNamespace(
        customers=SimpleNamespace(get_async=AsyncMock(side_effect=RuntimeError("down")))
    )
    monkeypatch.setattr(polar_service, "get_polar_client", lambda: client)

    with pytest.raises(RuntimeError, match="down"):
        await polar_service._resolve_user_from_polar_customer(
            db_session, "missing_customer"
        )


async def test_unknown_product_never_replaces_existing_entitlement(
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_product_ids({"tier1": "product_1", "tier2": "product_2"})
    test_user.polar_customer_id = "customer_test"
    db_session.add(_subscription(test_user.id, tier="tier2"))
    await db_session.commit()
    monkeypatch.setattr(polar_service, "_product_ids_configured", True)

    event = SimpleNamespace(
        TYPE="subscription.updated",
        timestamp=datetime.now(timezone.utc),
        data=SimpleNamespace(
            id="subscription_test",
            customer_id="customer_test",
            product_id="product_unknown",
            status="active",
            pending_update=None,
        ),
    )
    await polar_service.handle_polar_event(db_session, event)
    await db_session.commit()

    row = await crud_subscription.get_by_user(db_session, test_user.id)
    assert row is not None
    assert row.tier == "tier2"
    assert row.status == "active"


@pytest.mark.parametrize(
    ("current", "target", "expected_behavior"),
    [
        (Tier.tier1, Tier.tier2, "invoice"),
        (Tier.tier2, Tier.tier1, "next_period"),
    ],
)
async def test_change_plan_uses_directional_proration(
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
    current: Tier,
    target: Tier,
    expected_behavior: str,
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(polar_service, "_product_ids_configured", False)
    monkeypatch.setattr(settings, "POLAR_PRODUCT_TIER1_ID", "product_1")
    monkeypatch.setattr(settings, "POLAR_PRODUCT_TIER2_ID", "product_2")
    monkeypatch.setattr(settings, "POLAR_PRODUCT_TIER3_ID", "product_3")
    test_user.polar_customer_id = "customer_test"
    db_session.add(_subscription(test_user.id, tier=current.value))
    await db_session.commit()

    update = AsyncMock(
        return_value=SimpleNamespace(
            pending_update=(
                SimpleNamespace(applies_at=datetime.now(timezone.utc))
                if expected_behavior == "next_period"
                else None
            )
        )
    )
    client = SimpleNamespace(subscriptions=SimpleNamespace(update_async=update))
    monkeypatch.setattr(polar_service, "get_polar_client", lambda: client)

    result = await polar_service.change_plan(db_session, test_user, target)

    request = update.await_args.kwargs["subscription_update"]
    assert request["proration_behavior"].value == expected_behavior
    assert result.effective == (
        "immediate" if expected_behavior == "invoice" else "next_period"
    )


async def test_checkout_rejects_existing_live_subscription(
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    db_session.add(_subscription(test_user.id))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await polar_service.create_checkout(db_session, test_user, Tier.tier2)
    assert exc_info.value.status_code == 409
