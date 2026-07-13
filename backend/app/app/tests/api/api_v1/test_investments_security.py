from datetime import datetime, timezone
import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.core.config import Settings, settings
from app.schemas.user import UserCreate
from app.schemas.asset_price import AssetPriceCreate
from app.models.asset import Currency
from app.services.asset_resolver import AssetResolverService
from app.services.currency_converter import CurrencyConverter
from app.services.price_fetcher import PriceFetcher
from app.tests.utils.user import user_authentication_headers
from app.tests.utils.utils import random_email, random_lower_string


pytestmark = pytest.mark.asyncio
BASE_URL = f"{settings.API_V1_STR}/investments"


@pytest_asyncio.fixture(autouse=True)
async def enable_investments_for_existing_tests(
    monkeypatch: pytest.MonkeyPatch,
    async_get_db: AsyncSession,
    normal_user_token_headers: dict[str, str],
) -> None:
    user = await crud.user.get_by_email(async_get_db, email=settings.EMAIL_TEST_USER)
    assert user is not None
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", True)
    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_IDS", str(user.id))
    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_UUIDS", "")


@pytest.mark.parametrize(
    "path",
    ["/assets", "/holdings", "/transactions", "/portfolio/summary"],
)
async def test_investment_router_denies_every_subrouter_when_disabled(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", False)
    response = await client.get(f"{BASE_URL}{path}", headers=normal_user_token_headers)
    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Investments feature access is not enabled for this user"
    )


async def test_investment_access_supports_id_uuid_and_superuser_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import deps

    normal_user = SimpleNamespace(
        id=7, uuid=" user_uuid ", is_active=True, is_superuser=False
    )
    superuser = SimpleNamespace(id=99, uuid=None, is_active=True, is_superuser=True)

    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", False)
    with pytest.raises(HTTPException) as exc_info:
        deps.require_investments_access(superuser)
    assert exc_info.value.status_code == 403

    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", True)
    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_IDS", " 3, 7, 7 ")
    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_UUIDS", "other,user_uuid")
    assert deps.require_investments_access(normal_user) is normal_user

    monkeypatch.setattr(settings, "INVESTMENTS_ALLOWED_USER_IDS", "")
    # UUID matching is exact; use the stored value without normalization.
    normal_user.uuid = "user_uuid"
    assert deps.require_investments_access(normal_user) is normal_user
    assert deps.require_investments_access(superuser) is superuser

    normal_user.uuid = "USER_UUID"
    with pytest.raises(HTTPException) as exc_info:
        deps.require_investments_access(normal_user)
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("value", ["abc", "1,two", "0", "-4"])
async def test_invalid_investment_id_allowlist_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        Settings.validate_investment_user_ids(value)


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/assets", {"symbol": "SECURE", "name": "Secure", "asset_type": "stock"}),
        ("PUT", "/assets/1", {"name": "Tampered"}),
        ("DELETE", "/assets/1", None),
    ],
)
async def test_normal_user_cannot_mutate_global_assets(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    response = await client.request(
        method,
        f"{BASE_URL}{path}",
        headers=normal_user_token_headers,
        json=payload,
    )
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
        f"/assets/search-external?q={'x' * 101}",
    ],
)
async def test_investment_queries_are_bounded(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    path: str,
) -> None:
    response = await client.get(f"{BASE_URL}{path}", headers=normal_user_token_headers)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/assets/search?q=%20%20%20",
        "/assets/search-external?q=%20%20%20",
        "/assets/search-crypto?q=%20%20%20",
    ],
)
async def test_blank_search_queries_are_rejected_without_upstream_work(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    path: str,
) -> None:
    response = await client.get(f"{BASE_URL}{path}", headers=normal_user_token_headers)
    assert response.status_code == 422


async def test_holding_rejects_client_owned_valuation_fields(
    client: AsyncClient, normal_user_token_headers: dict[str, str]
) -> None:
    response = await client.put(
        f"{BASE_URL}/holdings/1",
        headers=normal_user_token_headers,
        json={"current_value_usd": 999999999},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("fees", -1),
        ("quantity", -1),
        ("exchange_rate_to_usd", 0),
        ("total_amount", 1000000),
        ("quantity", 1e16),
        ("price_per_unit", 1e16),
    ],
)
async def test_transaction_rejects_unsafe_financial_inputs(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    field: str,
    value: float,
) -> None:
    payload = {
        "holding_id": 1,
        "account_id": 1,
        "transaction_type": "buy",
        "quantity": 1,
        "price_per_unit": 10,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        field: value,
    }
    response = await client.post(
        f"{BASE_URL}/transactions",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert response.status_code == 422


async def test_transaction_rejects_ambiguous_asset_identity_and_naive_time(
    client: AsyncClient, normal_user_token_headers: dict[str, str]
) -> None:
    payload = {
        "asset_id": 1,
        "provider": "yahoo",
        "external_id": "AAPL",
        "account_id": 1,
        "transaction_type": "buy",
        "quantity": 1,
        "price_per_unit": 10,
        "executed_at": "2026-01-01T10:00:00",
    }
    response = await client.post(
        f"{BASE_URL}/transactions/with-asset",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert response.status_code == 422


async def test_price_refresh_rolls_back_all_shared_state_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    asset = SimpleNamespace(id=1, symbol="SAFE")
    price = AssetPriceCreate(
        asset_id=1,
        price=10,
        currency=Currency.USD,
        price_usd=10,
        price_mxn=180,
    )
    created_price = SimpleNamespace(id=1)
    monkeypatch.setattr(PriceFetcher, "fetch_price", AsyncMock(return_value=price))
    monkeypatch.setattr(
        crud.asset_price,
        "create_with_commit",
        AsyncMock(return_value=created_price),
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


async def test_cross_user_investment_ids_do_not_expose_or_mutate_data(
    client: AsyncClient,
    async_get_db: AsyncSession,
    normal_user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = random_lower_string()
    email = random_email()
    second_user = await crud.user.create(
        async_get_db,
        obj_in=UserCreate(
            email=email,
            password=password,
            name="Second User",
            country="EN-US",
        ),
    )
    assert second_user.id is not None
    second_headers = await user_authentication_headers(
        client=client, email=email, password=password
    )
    monkeypatch.setattr(
        settings,
        "INVESTMENTS_ALLOWED_USER_IDS",
        f"{settings.INVESTMENTS_ALLOWED_USER_IDS},{second_user.id}",
    )

    first_account_response = await client.post(
        f"{settings.API_V1_STR}/accounts",
        headers=normal_user_token_headers,
        json={"name": "First investments"},
    )
    second_account_response = await client.post(
        f"{settings.API_V1_STR}/accounts",
        headers=second_headers,
        json={"name": "Second investments"},
    )
    assert first_account_response.status_code == 200
    assert second_account_response.status_code == 200
    first_account_id = first_account_response.json()["id"]
    second_account_id = second_account_response.json()["id"]

    resolver_mock = AsyncMock()
    monkeypatch.setattr(AssetResolverService, "resolve_from_yahoo", resolver_mock)
    unauthorized_resolution = await client.post(
        f"{BASE_URL}/transactions/with-asset",
        headers=normal_user_token_headers,
        json={
            "account_id": second_account_id,
            "provider": "yahoo",
            "external_id": "AAPL",
            "transaction_type": "buy",
            "quantity": 1,
            "price_per_unit": 10,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert unauthorized_resolution.status_code == 404
    resolver_mock.assert_not_awaited()

    symbol = f"SEC{random_lower_string()[:8]}".upper()
    asset_response = await client.post(
        f"{BASE_URL}/assets",
        headers=superuser_token_headers,
        json={"symbol": symbol, "name": symbol, "asset_type": "stock"},
    )
    assert asset_response.status_code == 200
    asset_id = asset_response.json()["id"]

    unheld_symbol = f"UNH{random_lower_string()[:8]}".upper()
    unheld_asset_response = await client.post(
        f"{BASE_URL}/assets",
        headers=superuser_token_headers,
        json={
            "symbol": unheld_symbol,
            "name": unheld_symbol,
            "asset_type": "stock",
        },
    )
    assert unheld_asset_response.status_code == 200
    unheld_asset_id = unheld_asset_response.json()["id"]
    forbidden_refresh = await client.get(
        f"{BASE_URL}/assets/{unheld_asset_id}/price?refresh=true",
        headers=normal_user_token_headers,
    )
    assert forbidden_refresh.status_code == 403

    first_holding_response = await client.post(
        f"{BASE_URL}/holdings",
        headers=normal_user_token_headers,
        json={
            "account_id": first_account_id,
            "asset_id": asset_id,
            "quantity": 2,
            "avg_cost_basis": 10,
        },
    )
    second_holding_response = await client.post(
        f"{BASE_URL}/holdings",
        headers=second_headers,
        json={
            "account_id": second_account_id,
            "asset_id": asset_id,
            "quantity": 3,
            "avg_cost_basis": 20,
        },
    )
    assert first_holding_response.status_code == 200
    assert second_holding_response.status_code == 200
    first_holding_id = first_holding_response.json()["id"]
    second_holding_id = second_holding_response.json()["id"]

    for method in ("GET", "PUT", "DELETE"):
        response = await client.request(
            method,
            f"{BASE_URL}/holdings/{second_holding_id}",
            headers=normal_user_token_headers,
            json={"quantity": 99} if method == "PUT" else None,
        )
        assert response.status_code == 404

    filtered = await client.get(
        f"{BASE_URL}/holdings?account_id={second_account_id}",
        headers=normal_user_token_headers,
    )
    assert filtered.status_code == 404

    wrong_account = await client.post(
        f"{BASE_URL}/transactions",
        headers=normal_user_token_headers,
        json={
            "account_id": second_account_id,
            "holding_id": first_holding_id,
            "transaction_type": "buy",
            "quantity": 1,
            "price_per_unit": 10,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert wrong_account.status_code == 404

    monkeypatch.setattr(
        CurrencyConverter,
        "get_usd_to_mxn_rate",
        AsyncMock(return_value=18.0),
    )
    transaction_response = await client.post(
        f"{BASE_URL}/transactions",
        headers=normal_user_token_headers,
        json={
            "account_id": first_account_id,
            "holding_id": first_holding_id,
            "transaction_type": "buy",
            "quantity": 1,
            "price_per_unit": 10,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert transaction_response.status_code == 200
    transaction_id = transaction_response.json()["id"]

    for method in ("GET", "DELETE"):
        response = await client.request(
            method,
            f"{BASE_URL}/transactions/{transaction_id}",
            headers=second_headers,
        )
        assert response.status_code == 404

    sell_payload = {
        "account_id": first_account_id,
        "holding_id": first_holding_id,
        "transaction_type": "sell",
        "quantity": 2,
        "price_per_unit": 10,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
    concurrent_sells = await asyncio.gather(
        client.post(
            f"{BASE_URL}/transactions",
            headers=normal_user_token_headers,
            json=sell_payload,
        ),
        client.post(
            f"{BASE_URL}/transactions",
            headers=normal_user_token_headers,
            json=sell_payload,
        ),
    )
    assert sorted(response.status_code for response in concurrent_sells) == [200, 400]
    final_holding = await client.get(
        f"{BASE_URL}/holdings/{first_holding_id}",
        headers=normal_user_token_headers,
    )
    assert final_holding.status_code == 200
    assert final_holding.json()["quantity"] == 1

    first_summary = await client.get(
        f"{BASE_URL}/portfolio/summary", headers=normal_user_token_headers
    )
    second_summary = await client.get(
        f"{BASE_URL}/portfolio/summary", headers=second_headers
    )
    assert first_summary.status_code == 200
    assert second_summary.status_code == 200
    assert first_summary.json()["total_holdings"] == 1
    assert second_summary.json()["total_holdings"] == 1
