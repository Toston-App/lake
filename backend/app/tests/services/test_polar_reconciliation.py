from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.tiers import configure_product_ids
from app.models.subscription import UserSubscription
from app.models.user import User
from app.services.polar_reconciliation import reconcile_subscriptions
from sqlalchemy.ext.asyncio import AsyncSession


def _remote(*, status: str = "active") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id="subscription_reconcile",
        customer_id="customer_reconcile",
        product_id="product_1",
        status=status,
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=29),
        cancel_at_period_end=False,
        canceled_at=None,
        ends_at=None,
        ended_at=None,
    )


async def _local_row(
    db_session: AsyncSession, test_user: User, remote: SimpleNamespace
) -> UserSubscription:
    row = UserSubscription(
        user_id=test_user.id,
        polar_customer_id=remote.customer_id,
        polar_subscription_id=remote.id,
        tier="tier1",
        status=remote.status,
        current_period_start=remote.current_period_start,
        current_period_end=remote.current_period_end,
        cancel_at_period_end=remote.cancel_at_period_end,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def test_reconciliation_noop(
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_product_ids({"tier1": "product_1"})
    remote = _remote()
    await _local_row(db_session, test_user, remote)
    monkeypatch.setattr(
        "app.services.polar_reconciliation.get_entitlement",
        AsyncMock(return_value=None),
    )
    polar = SimpleNamespace(
        subscriptions=SimpleNamespace(get_async=AsyncMock(return_value=remote))
    )

    report = await reconcile_subscriptions(db_session, polar=polar)

    assert report.as_dict()["counts"]["drifted"] == 0
    assert report.repaired_user_ids == []


async def test_reconciliation_detects_drift_without_repairing(
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_product_ids({"tier1": "product_1"})
    remote = _remote(status="canceled")
    row = await _local_row(db_session, test_user, remote)
    row.status = "active"
    await db_session.commit()
    monkeypatch.setattr(
        "app.services.polar_reconciliation.get_entitlement",
        AsyncMock(return_value=None),
    )
    polar = SimpleNamespace(
        subscriptions=SimpleNamespace(get_async=AsyncMock(return_value=remote))
    )

    report = await reconcile_subscriptions(db_session, polar=polar)

    assert report.drifted_user_ids == [test_user.id]
    assert row.status == "active"


async def test_reconciliation_explicit_repair_updates_state_and_cache(
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_product_ids({"tier1": "product_1"})
    remote = _remote(status="canceled")
    row = await _local_row(db_session, test_user, remote)
    row.status = "active"
    await db_session.commit()
    monkeypatch.setattr(
        "app.services.polar_reconciliation.get_entitlement",
        AsyncMock(return_value={"tier": "tier1"}),
    )
    cache_write = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.polar_reconciliation.set_entitlement", cache_write
    )
    polar = SimpleNamespace(
        subscriptions=SimpleNamespace(get_async=AsyncMock(return_value=remote))
    )

    report = await reconcile_subscriptions(db_session, polar=polar, repair=True)

    assert row.status == "canceled"
    assert report.repaired_user_ids == [test_user.id]
    cache_write.assert_awaited_once()
