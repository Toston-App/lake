# ruff: noqa: ARG001
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.api import deps
from app.core.config import Settings, settings
from app.models.asset import Currency
from app.schemas.asset_price import AssetPriceCreate
from app.services.price_fetcher import PriceFetcher
from fastapi import HTTPException
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


def test_access_supports_id_uuid_and_superuser(monkeypatch):
    user = SimpleNamespace(id=7, uuid="user_uuid", is_superuser=False)
    admin = SimpleNamespace(id=99, uuid=None, is_superuser=True)
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", True)
    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_IDS", "3, 7")
    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_UUIDS", "")
    assert deps.require_investments_access(user) is user
    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_IDS", "")
    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_UUIDS", "user_uuid")
    assert deps.require_investments_access(user) is user
    assert deps.require_investments_access(admin) is admin
    user.uuid = "USER_UUID"
    with pytest.raises(HTTPException):
        deps.require_investments_access(user)


@pytest.mark.parametrize("value", ["abc", "1,two", "0", "-4"])
def test_invalid_allowlist_is_rejected(value):
    with pytest.raises(ValueError):
        Settings.validate_investment_user_ids(value)


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
