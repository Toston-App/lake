# ruff: noqa: ARG001
from decimal import Decimal
from unittest.mock import AsyncMock

from app.models.asset import AssetType, Market
from app.services.coingecko import CoinGeckoService
from app.services.price_fetcher import PriceFetcher
from app.services.yahoo_finance import YahooFinanceService
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_asset_price,
    create_test_holding,
)

PREFIX = "/api/v1/investments/assets"


async def test_list_filters_and_pagination(
    client: AsyncClient, db_session: AsyncSession, enable_investments
):
    await create_test_asset(db_session, symbol="AAA")
    await create_test_asset(
        db_session,
        symbol="BTC",
        name="Bitcoin",
        asset_type=AssetType.CRYPTOCURRENCY,
        market=Market.CRYPTO,
        coingecko_id="bitcoin",
    )
    response = await client.get(f"{PREFIX}?asset_class=crypto&limit=1")
    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["BTC"]
    assert (await client.get(f"{PREFIX}?limit=101")).status_code == 422
    assert (await client.get(f"{PREFIX}?skip=-1")).status_code == 422


async def test_normal_user_cannot_create_global_asset(
    client: AsyncClient, enable_investments
):
    payload = {"symbol": "api", "name": "API Asset", "asset_type": "stock"}
    assert (await client.post(PREFIX, json=payload)).status_code == 400


async def test_superuser_mutations(
    superuser_client: AsyncClient,
    db_session: AsyncSession,
    enable_investments,
):
    payload = {"symbol": "api", "name": "API Asset", "asset_type": "stock"}
    created = await superuser_client.post(PREFIX, json=payload)
    assert created.status_code == 200
    asset_id = created.json()["id"]
    assert created.json()["symbol"] == "API"
    assert (await superuser_client.post(PREFIX, json=payload)).status_code == 400
    updated = await superuser_client.put(
        f"{PREFIX}/{asset_id}", json={"name": "Updated"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated"
    deleted = await superuser_client.delete(f"{PREFIX}/{asset_id}")
    assert deleted.status_code == 200
    asset = await db_session.get(
        __import__("app.models.asset", fromlist=["Asset"]).Asset, asset_id
    )
    assert asset.is_active is False


async def test_local_search_and_blank_rejection(
    client: AsyncClient, db_session: AsyncSession, enable_investments
):
    await create_test_asset(db_session, symbol="MSFT", name="Microsoft")
    response = await client.get(f"{PREFIX}/search?q=soft")
    assert response.status_code == 200
    assert response.json()[0]["symbol"] == "MSFT"
    assert (await client.get(f"{PREFIX}/search?q=%20%20")).status_code == 422


async def test_external_search_filters_and_normalizes_mexican_symbol(
    client: AsyncClient, monkeypatch, enable_investments
):
    monkeypatch.setattr(
        YahooFinanceService,
        "search_symbol",
        AsyncMock(
            return_value=[
                {
                    "symbol": "WALMEX.MX",
                    "name": "Walmart Mexico",
                    "exchange": "MEX",
                    "type": "EQUITY",
                },
                {"symbol": "BAD", "name": "Bad", "exchange": "LSE", "type": "EQUITY"},
                {"symbol": "SPY", "name": "SPDR", "exchange": "PCX", "type": "ETF"},
            ]
        ),
    )
    response = await client.get(f"{PREFIX}/search-external?q=wal")
    assert response.status_code == 200
    data = response.json()
    assert [item["symbol"] for item in data] == ["WALMEX", "SPY"]
    assert data[0]["market"] == "BMV"
    assert data[0]["currency"] == "MXN"


async def test_crypto_search(client: AsyncClient, monkeypatch, enable_investments):
    monkeypatch.setattr(
        CoinGeckoService,
        "search_coins",
        AsyncMock(
            return_value=[
                {
                    "coingecko_id": "bitcoin",
                    "symbol": "BTC",
                    "name": "Bitcoin",
                    "market_cap_rank": 1,
                }
            ]
        ),
    )
    response = await client.get(f"{PREFIX}/search-crypto?q=bit")
    assert response.status_code == 200
    assert response.json()[0]["provider"] == "coingecko"
    assert response.json()[0]["market_cap_rank"] == 1


async def test_get_asset_includes_latest_price_and_404(
    client: AsyncClient, db_session: AsyncSession, enable_investments
):
    asset = await create_test_asset(db_session, symbol="DETAIL")
    await create_test_asset_price(db_session, asset_id=asset.id, price=Decimal("123"))
    response = await client.get(f"{PREFIX}/{asset.id}")
    assert response.status_code == 200
    assert Decimal(response.json()["current_price"]) == Decimal("123")
    assert (await client.get(f"{PREFIX}/999999")).status_code == 404


async def test_price_cache_refresh_permissions_and_inactive_guard(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user,
    monkeypatch,
    enable_investments,
):
    asset = await create_test_asset(db_session, symbol="FETCH")
    price = await create_test_asset_price(db_session, asset_id=asset.id)
    monkeypatch.setattr(
        PriceFetcher, "get_current_price", AsyncMock(return_value=price)
    )
    cached = await client.get(f"{PREFIX}/{asset.id}/price")
    assert cached.status_code == 200
    assert Decimal(cached.json()["price"]) == Decimal("100")
    assert (
        await client.get(f"{PREFIX}/{asset.id}/price?refresh=true")
    ).status_code == 403

    account = await create_test_account(db_session, owner_id=test_user.id)
    await create_test_holding(
        db_session, owner_id=test_user.id, account_id=account.id, asset_id=asset.id
    )
    monkeypatch.setattr(
        PriceFetcher, "fetch_and_store_price", AsyncMock(return_value=price)
    )
    refreshed = await client.get(f"{PREFIX}/{asset.id}/price?refresh=true")
    assert refreshed.status_code == 200

    asset.is_active = False
    db_session.add(asset)
    await db_session.commit()
    assert (
        await client.get(f"{PREFIX}/{asset.id}/price?refresh=true")
    ).status_code == 409


async def test_bulk_refresh_and_global_permission(
    client: AsyncClient, monkeypatch, enable_investments
):
    refresh = AsyncMock(return_value=(2, ["FAILED"]))
    monkeypatch.setattr(PriceFetcher, "refresh_all_prices", refresh)
    response = await client.post(f"{PREFIX}/refresh-prices")
    assert response.status_code == 200
    assert response.json()["updated_count"] == 2
    assert response.json()["failed_symbols"] == ["FAILED"]
    assert (
        await client.post(f"{PREFIX}/refresh-prices?only_my_holdings=false")
    ).status_code == 403
