# ruff: noqa: ARG001
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.config import settings
from app.models.asset import Currency
from app.models.subscription import UserSubscription
from app.schemas.asset_price import AssetPriceCreate
from app.services.price_fetcher import PriceFetcher
from httpx import AsyncClient

from app import crud

BASE = "/api/v1/investments"


@pytest.mark.parametrize(
    "path", ["/assets", "/holdings", "/transactions", "/portfolio/summary"]
)
async def test_feature_gate_denies_all_subrouters_when_disabled(
    client: AsyncClient, monkeypatch, path
):
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", False)
    response = await client.get(f"{BASE}{path}")
    assert response.status_code == 403


@pytest.fixture
def billing_on(monkeypatch):
    """Resolve tiers from user_subscription rows, bypassing the Redis cache."""
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", True)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(
        "app.services.entitlements.get_entitlement", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.entitlements.set_entitlement", AsyncMock(return_value=True)
    )


async def _subscribe(db_session, user, *, tier: str, status: str = "active"):
    now = datetime.now(timezone.utc)
    db_session.add(
        UserSubscription(
            user_id=user.id,
            polar_customer_id=f"customer_{user.id}",
            polar_subscription_id=f"subscription_{user.id}",
            tier=tier,
            status=status,
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    await db_session.commit()


@pytest.mark.parametrize("tier", ["tier1", "tier2"])
async def test_any_paid_tier_can_use_investments(
    client: AsyncClient, db_session, test_user, billing_on, tier
):
    await _subscribe(db_session, test_user, tier=tier)

    response = await client.get(f"{BASE}/holdings")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "path", ["/assets", "/holdings", "/transactions", "/portfolio/summary"]
)
async def test_free_user_is_asked_to_subscribe(client: AsyncClient, billing_on, path):
    response = await client.get(f"{BASE}{path}")

    assert response.status_code == 402
    assert response.json()["detail"] == (
        "Investments require an active paid subscription"
    )


@pytest.mark.parametrize("status", ["revoked", "past_due", "canceled"])
async def test_lapsed_subscription_loses_investments(
    client: AsyncClient, db_session, test_user, billing_on, status
):
    await _subscribe(db_session, test_user, tier="tier2", status=status)

    response = await client.get(f"{BASE}/holdings")

    assert response.status_code == 402


async def test_superuser_needs_no_subscription(
    superuser_client: AsyncClient, billing_on
):
    response = await superuser_client.get(f"{BASE}/holdings")

    assert response.status_code == 200


async def test_production_without_billing_denies_investments(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", True)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)
    # What the production settings validator forces when Polar is off.
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "free")

    response = await client.get(f"{BASE}/holdings")

    assert response.status_code == 402


@pytest.mark.parametrize(
    "method,path,payload",
    [
        (
            "POST",
            "/assets",
            {"symbol": "SECURE", "name": "Secure", "asset_type": "stock"},
        ),
        ("PUT", "/assets/1", {"name": "Tampered"}),
        ("DELETE", "/assets/1", None),
    ],
)
async def test_normal_user_cannot_mutate_assets(
    client, enable_investments, method, path, payload
):
    response = await client.request(method, f"{BASE}{path}", json=payload)
    assert response.status_code in (400, 403)


@pytest.mark.parametrize(
    "path",
    [
        "/assets?limit=101",
        "/assets?skip=-1",
        "/holdings?limit=101",
        "/holdings?account_id=0",
        "/transactions?limit=101",
        "/transactions?holding_id=0",
        "/transactions?account_id=0",
        "/holdings/0",
        "/transactions/0",
        f"/assets/search?q={'x' * 101}",
    ],
)
async def test_queries_are_bounded(client, enable_investments, path):
    assert (await client.get(f"{BASE}{path}")).status_code == 422


@pytest.mark.parametrize(
    "path", ["/assets/search", "/assets/search-external", "/assets/search-crypto"]
)
async def test_blank_search_is_rejected(client, enable_investments, path):
    assert (await client.get(f"{BASE}{path}?q=%20%20")).status_code == 422


async def test_rejects_client_owned_or_unsafe_financial_fields(
    client, enable_investments
):
    assert (
        await client.put(f"{BASE}/holdings/1", json={"current_value_usd": 999})
    ).status_code == 422
    base = {
        "holding_id": 1,
        "account_id": 1,
        "transaction_type": "buy",
        "quantity": 1,
        "price_per_unit": 10,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
    for field, value in (("fees", -1), ("quantity", 1e16), ("total_amount", 5)):
        response = await client.post(
            f"{BASE}/transactions",
            headers={"Idempotency-Key": f"unsafe-{field}"},
            json={**base, field: value},
        )
        assert response.status_code == 422


async def test_price_refresh_rolls_back_shared_state(monkeypatch):
    db = AsyncMock()
    asset = SimpleNamespace(id=1, symbol="SAFE")
    price = AssetPriceCreate(
        asset_id=1, price=10, currency=Currency.USD, price_usd=10, price_mxn=180
    )
    monkeypatch.setattr(PriceFetcher, "fetch_price", AsyncMock(return_value=price))
    monkeypatch.setattr(
        crud.asset_price,
        "create_with_commit",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    monkeypatch.setattr(
        PriceFetcher,
        "_update_holdings_for_asset",
        AsyncMock(side_effect=RuntimeError("forced failure")),
    )
    with pytest.raises(RuntimeError, match="forced failure"):
        await PriceFetcher.fetch_and_store_price(db, asset)
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


def test_every_paid_tier_and_only_paid_tiers_include_investments():
    from app.core.tiers import FEATURE_INVESTMENTS, Tier, has_feature

    assert not has_feature(Tier.free, FEATURE_INVESTMENTS)
    assert all(
        has_feature(tier, FEATURE_INVESTMENTS) for tier in Tier if tier != Tier.free
    )
