from uuid import uuid4

import pytest
from app.models.asset import AssetClass, AssetType, Currency, Market
from app.schemas.asset import AssetCreate
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from tests.utils import create_test_asset


async def test_symbol_and_coingecko_lookups_normalize(db_session: AsyncSession):
    asset = await create_test_asset(db_session, symbol="AAPL", coingecko_id="bitcoin")
    assert await crud.asset.get_by_symbol(db_session, symbol="aapl") == asset
    assert (
        await crud.asset.get_by_coingecko_id(db_session, coingecko_id="BITCOIN")
        == asset
    )


async def test_get_multi_filtered_supports_every_filter(db_session: AsyncSession):
    await create_test_asset(db_session, symbol="AAPL")
    await create_test_asset(
        db_session,
        symbol="BTC",
        name="Bitcoin",
        asset_type=AssetType.CRYPTOCURRENCY,
        currency=Currency.MXN,
        market=Market.CRYPTO,
        coingecko_id="bitcoin",
    )
    await create_test_asset(db_session, symbol="OLD", is_active=False)

    cases = [
        ({"asset_class": AssetClass.CRYPTO}, "BTC"),
        ({"asset_type": AssetType.CRYPTOCURRENCY}, "BTC"),
        ({"currency": Currency.MXN}, "BTC"),
        ({"market": Market.CRYPTO}, "BTC"),
        ({"is_active": False}, "OLD"),
    ]
    for filters, symbol in cases:
        assets = await crud.asset.get_multi_filtered(db_session, **filters)
        assert [asset.symbol for asset in assets] == [symbol]


async def test_search_assets_matches_and_escapes_wildcards(db_session: AsyncSession):
    await create_test_asset(db_session, symbol="MSFT", name="Microsoft")
    await create_test_asset(db_session, symbol="PCT%", name="Percent")
    await create_test_asset(db_session, symbol="UNDER_", name="Underscore")
    assert [
        a.symbol for a in await crud.asset.search_assets(db_session, query="soft")
    ] == ["MSFT"]
    assert [
        a.symbol for a in await crud.asset.search_assets(db_session, query="%")
    ] == ["PCT%"]
    assert [
        a.symbol for a in await crud.asset.search_assets(db_session, query="_")
    ] == ["UNDER_"]


async def test_create_uppercases_and_get_or_create(db_session: AsyncSession):
    unique = uuid4().hex[:8]
    data = AssetCreate(symbol=f"x{unique}", name="Created", asset_type=AssetType.ETF)
    asset = await crud.asset.create(db_session, obj_in=data)
    assert asset.symbol == f"X{unique.upper()}"
    assert asset.asset_class == AssetClass.EQUITIES
    same, created = await crud.asset.get_or_create(db_session, obj_in=data)
    assert same.id == asset.id
    assert created is False


async def test_duplicate_symbol_is_rejected(db_session: AsyncSession):
    await create_test_asset(db_session, symbol="DUP")
    with pytest.raises(IntegrityError):
        await crud.asset.create(
            db_session,
            obj_in=AssetCreate(
                symbol="DUP", name="Duplicate", asset_type=AssetType.STOCK
            ),
        )
