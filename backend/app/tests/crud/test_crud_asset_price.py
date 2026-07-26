from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.asset import Currency
from app.schemas.asset_price import AssetPriceCreate
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from tests.utils import create_test_asset, create_test_asset_price


async def test_create_and_latest_ordering(db_session: AsyncSession):
    asset = await create_test_asset(db_session, symbol="PRICE")
    created = await crud.asset_price.create_with_commit(
        db_session,
        obj_in=AssetPriceCreate(
            asset_id=asset.id,
            price=Decimal("10"),
            currency=Currency.USD,
            price_usd=Decimal("10"),
            price_mxn=Decimal("180"),
        ),
    )
    newer = await create_test_asset_price(
        db_session, asset_id=asset.id, price=Decimal("11")
    )
    assert created.id is not None
    assert (
        await crud.asset_price.get_latest_by_asset(db_session, asset_id=asset.id)
    ).id == newer.id


async def test_get_latest_prices_uses_one_per_asset(db_session: AsyncSession):
    first = await create_test_asset(db_session, symbol="ONE")
    second = await create_test_asset(db_session, symbol="TWO")
    old = datetime.now(timezone.utc) - timedelta(days=1)
    await create_test_asset_price(
        db_session, asset_id=first.id, price=Decimal("1"), fetched_at=old
    )
    latest = await create_test_asset_price(
        db_session, asset_id=first.id, price=Decimal("2")
    )
    other = await create_test_asset_price(
        db_session, asset_id=second.id, price=Decimal("3")
    )
    prices = await crud.asset_price.get_latest_prices(
        db_session, asset_ids=[first.id, second.id]
    )
    assert prices[first.id].id == latest.id
    assert prices[second.id].id == other.id
    assert await crud.asset_price.get_latest_prices(db_session, asset_ids=[]) == {}


async def test_is_stale_for_missing_fresh_and_old_prices(db_session: AsyncSession):
    missing = await create_test_asset(db_session, symbol="MISS")
    fresh = await create_test_asset(db_session, symbol="FRESH")
    stale = await create_test_asset(db_session, symbol="STALE")
    await create_test_asset_price(db_session, asset_id=fresh.id)
    await create_test_asset_price(
        db_session,
        asset_id=stale.id,
        fetched_at=datetime.now(timezone.utc) - timedelta(minutes=16),
    )
    assert await crud.asset_price.is_stale(db_session, asset_id=missing.id)
    assert not await crud.asset_price.is_stale(db_session, asset_id=fresh.id)
    assert await crud.asset_price.is_stale(db_session, asset_id=stale.id)


async def test_get_history_honors_range_and_order(db_session: AsyncSession):
    asset = await create_test_asset(db_session, symbol="HIST")
    now = datetime.now(timezone.utc)
    await create_test_asset_price(
        db_session,
        asset_id=asset.id,
        price=Decimal("1"),
        fetched_at=now - timedelta(days=2),
    )
    middle = await create_test_asset_price(
        db_session,
        asset_id=asset.id,
        price=Decimal("2"),
        fetched_at=now - timedelta(days=1),
    )
    latest = await create_test_asset_price(
        db_session, asset_id=asset.id, price=Decimal("3"), fetched_at=now
    )
    history = await crud.asset_price.get_history(
        db_session,
        asset_id=asset.id,
        start_date=now - timedelta(days=1, minutes=1),
        end_date=now + timedelta(minutes=1),
    )
    assert [p.id for p in history] == [latest.id, middle.id]
