# ruff: noqa: ARG001
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.models.asset import AssetType, Currency, Market
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.services import portfolio_snapshot as snapshot_service
from app.services.currency_converter import CurrencyConverter, CurrencyRateUnavailable
from app.services.portfolio_snapshot import (
    compute_portfolio_totals,
    held_active_asset_ids,
    portfolio_today,
    snapshot_all_owners,
    snapshot_owner,
)
from app.services.price_fetcher import PriceFetcher
from app.worker import portfolio_snapshot_worker
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_holding,
    create_test_user,
)

from app import crud

RATE = Decimal("18")
DAY = date(2026, 1, 15)


def _fake_session_factory(db_session):
    @asynccontextmanager
    async def _session():
        yield db_session

    return _session


def _holding(
    id: int,
    *,
    quantity: str,
    value_usd: str,
    value_mxn: str,
    invested: str,
    cost_currency: Currency,
):
    return SimpleNamespace(
        id=id,
        asset_id=id * 10,
        quantity=Decimal(quantity),
        current_value_usd=Decimal(value_usd),
        current_value_mxn=Decimal(value_mxn),
        total_invested=Decimal(invested),
        cost_currency=cost_currency,
    )


async def _portfolio(db: AsyncSession, user):
    first_account = await create_test_account(db, owner_id=user.id, name="Brokerage")
    second_account = await create_test_account(db, owner_id=user.id, name="Crypto")
    stock = await create_test_asset(db, symbol="PORT", country="US")
    crypto = await create_test_asset(
        db,
        symbol="BTC",
        name="Bitcoin",
        asset_type=AssetType.CRYPTOCURRENCY,
        market=Market.CRYPTO,
        coingecko_id="bitcoin",
        country="GLOBAL",
    )
    first = await create_test_holding(
        db,
        owner_id=user.id,
        account_id=first_account.id,
        asset_id=stock.id,
        quantity=Decimal("1"),
        avg_cost_basis=Decimal("80"),
    )
    second = await create_test_holding(
        db,
        owner_id=user.id,
        account_id=second_account.id,
        asset_id=crypto.id,
        quantity=Decimal("1"),
        avg_cost_basis=Decimal("50"),
    )
    first.current_value = first.current_value_usd = Decimal("100")
    first.current_value_mxn = Decimal("1800")
    second.current_value = second.current_value_usd = Decimal("300")
    second.current_value_mxn = Decimal("5400")
    db.add_all([first, second])
    await db.commit()
    return first, second


async def _single_holding(db: AsyncSession, user, symbol: str = "SNAP"):
    account = await create_test_account(db, owner_id=user.id, name=f"{symbol} acct")
    asset = await create_test_asset(db, symbol=symbol)
    return await create_test_holding(
        db,
        owner_id=user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal("2"),
        avg_cost_basis=Decimal("50"),
    )


async def _snapshot_count(db: AsyncSession) -> int:
    return (
        await db.execute(select(func.count()).select_from(PortfolioSnapshot))
    ).scalar_one()


def test_compute_totals_empty():
    totals = compute_portfolio_totals([], RATE)
    assert totals.total_value_usd == totals.total_value_mxn == 0
    assert totals.total_invested_usd == totals.total_invested_mxn == 0
    assert totals.total_invested_combined_usd == 0
    assert totals.total_invested_combined_mxn == 0
    assert totals.holdings_count == 0
    assert totals.positions == ()


def test_compute_totals_mixed_cost_currencies():
    holdings = [
        _holding(
            1,
            quantity="2",
            value_usd="120",
            value_mxn="2160",
            invested="100",
            cost_currency=Currency.USD,
        ),
        _holding(
            2,
            quantity="1",
            value_usd="110",
            value_mxn="1980",
            invested="1800",
            cost_currency=Currency.MXN,
        ),
        _holding(
            3,
            quantity="0",
            value_usd="0",
            value_mxn="0",
            invested="0",
            cost_currency=Currency.USD,
        ),
    ]
    totals = compute_portfolio_totals(holdings, RATE)
    assert totals.total_value_usd == Decimal("230")
    assert totals.total_value_mxn == Decimal("4140")
    assert totals.total_invested_usd == Decimal("100")
    assert totals.total_invested_mxn == Decimal("1800")
    assert totals.total_invested_combined_usd == Decimal("200")
    assert totals.total_invested_combined_mxn == Decimal("3600")
    assert totals.holdings_count == 3
    assert [p.holding_id for p in totals.positions] == [1, 2]
    assert totals.positions[1].asset_id == 20
    assert totals.positions[1].value_mxn == Decimal("1980")


async def test_totals_match_summary_endpoint(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    await _portfolio(db_session, test_user)
    account = await create_test_account(db_session, owner_id=test_user.id, name="MX")
    asset = await create_test_asset(
        db_session, symbol="WALMEX", currency=Currency.MXN, market=Market.BMV
    )
    await create_test_holding(
        db_session,
        owner_id=test_user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal("1"),
        avg_cost_basis=Decimal("1800"),
        cost_currency=Currency.MXN,
        asset_currency=Currency.MXN,
    )

    response = await client.get("/api/v1/investments/portfolio/summary")
    assert response.status_code == 200
    data = response.json()
    holdings = await crud.holding.get_by_owner(
        db_session, owner_id=test_user.id, limit=None
    )
    totals = compute_portfolio_totals(holdings, RATE)
    for field in (
        "total_value_usd",
        "total_value_mxn",
        "total_invested_usd",
        "total_invested_mxn",
        "total_invested_combined_usd",
        "total_invested_combined_mxn",
    ):
        assert round(getattr(totals, field), 2) == Decimal(str(data[field])), field
    assert totals.total_invested_mxn > 0


async def test_upsert_replaces_same_day(db_session: AsyncSession, test_user):
    holding = await _single_holding(db_session, test_user)

    await snapshot_owner(
        db_session, owner_id=test_user.id, snapshot_date=DAY, usd_mxn_rate=RATE
    )
    holding.current_value_usd = Decimal("250")
    holding.current_value_mxn = Decimal("4500")
    db_session.add(holding)
    await db_session.commit()
    await snapshot_owner(
        db_session, owner_id=test_user.id, snapshot_date=DAY, usd_mxn_rate=RATE
    )

    rows = await crud.portfolio_snapshot.get_range(
        db_session, owner_id=test_user.id, start_date=None
    )
    assert len(rows) == 1
    assert rows[0].total_value_usd == Decimal("250")
    assert rows[0].total_value_mxn == Decimal("4500")
    assert rows[0].holdings_count == 1
    assert len(rows[0].positions) == 1
    position = rows[0].positions[0]
    assert position.holding_id == holding.id
    assert position.quantity == Decimal("2")
    assert position.value_usd == Decimal("250")
    assert position.value_mxn == Decimal("4500")


async def test_snapshot_all_owners_skips_owners_without_holdings(
    db_session: AsyncSession, test_user
):
    second = await create_test_user(db_session, email="second@example.com")
    third = await create_test_user(db_session, email="third@example.com")
    await _single_holding(db_session, test_user, "ONE")
    await _single_holding(db_session, second, "TWO")

    count = await snapshot_all_owners(db_session, snapshot_date=DAY, usd_mxn_rate=RATE)
    await db_session.commit()

    assert count == 2
    for user, expected in ((test_user, 1), (second, 1), (third, 0)):
        rows = await crud.portfolio_snapshot.get_range(
            db_session, owner_id=user.id, start_date=None
        )
        assert len(rows) == expected


async def test_snapshot_all_owners_isolates_failures(
    db_session: AsyncSession, test_user, monkeypatch: pytest.MonkeyPatch
):
    second = await create_test_user(db_session, email="second@example.com")
    await _single_holding(db_session, test_user, "ONE")
    await _single_holding(db_session, second, "TWO")
    real_snapshot_owner = snapshot_service.snapshot_owner

    async def flaky(db, *, owner_id, **kwargs):
        if owner_id == test_user.id:
            raise RuntimeError("boom")
        return await real_snapshot_owner(db, owner_id=owner_id, **kwargs)

    monkeypatch.setattr(snapshot_service, "snapshot_owner", flaky)

    count = await snapshot_all_owners(db_session, snapshot_date=DAY, usd_mxn_rate=RATE)
    await db_session.commit()

    assert count == 1
    assert await crud.portfolio_snapshot.get_range(
        db_session, owner_id=second.id, start_date=None
    )
    assert not await crud.portfolio_snapshot.get_range(
        db_session, owner_id=test_user.id, start_date=None
    )


async def test_get_range_and_latest_on_or_before(db_session: AsyncSession, test_user):
    await _single_holding(db_session, test_user)
    days = [DAY, DAY + timedelta(days=2), DAY + timedelta(days=5)]
    for day in reversed(days):
        await snapshot_owner(
            db_session, owner_id=test_user.id, snapshot_date=day, usd_mxn_rate=RATE
        )
    await db_session.commit()

    all_rows = await crud.portfolio_snapshot.get_range(
        db_session, owner_id=test_user.id, start_date=None
    )
    assert [r.snapshot_date for r in all_rows] == days
    filtered = await crud.portfolio_snapshot.get_range(
        db_session, owner_id=test_user.id, start_date=DAY + timedelta(days=1)
    )
    assert [r.snapshot_date for r in filtered] == days[1:]

    latest = await crud.portfolio_snapshot.get_latest_on_or_before(
        db_session, owner_id=test_user.id, day=DAY + timedelta(days=4)
    )
    assert latest is not None
    assert latest.snapshot_date == days[1]
    assert len(latest.positions) == 1
    assert (
        await crud.portfolio_snapshot.get_latest_on_or_before(
            db_session, owner_id=test_user.id, day=DAY - timedelta(days=1)
        )
        is None
    )


async def test_held_active_asset_ids(db_session: AsyncSession, test_user):
    held = await _single_holding(db_session, test_user, "HELD")
    sold = await _single_holding(db_session, test_user, "SOLD")
    sold.quantity = Decimal("0")
    sold.total_invested = Decimal("0")
    sold.current_value = sold.current_value_usd = sold.current_value_mxn = 0
    db_session.add(sold)
    account = await create_test_account(db_session, owner_id=test_user.id, name="Old")
    inactive = await create_test_asset(db_session, symbol="DEAD", is_active=False)
    await create_test_holding(
        db_session, owner_id=test_user.id, account_id=account.id, asset_id=inactive.id
    )
    await db_session.commit()

    assert await held_active_asset_ids(db_session) == [held.asset_id]


async def test_worker_skips_snapshots_without_fx(
    db_session: AsyncSession,
    test_user,
    enable_investments,
    monkeypatch: pytest.MonkeyPatch,
):
    await _single_holding(db_session, test_user)
    monkeypatch.setattr(
        CurrencyConverter,
        "get_usd_to_mxn_rate",
        AsyncMock(side_effect=CurrencyRateUnavailable("x")),
    )
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(PriceFetcher, "fetch_and_store_price", fetch)
    monkeypatch.setattr(
        portfolio_snapshot_worker, "async_session", _fake_session_factory(db_session)
    )

    await portfolio_snapshot_worker.tick()

    assert await _snapshot_count(db_session) == 0
    assert fetch.await_count >= 1


async def test_worker_writes_snapshot(
    db_session: AsyncSession,
    test_user,
    enable_investments,
    monkeypatch: pytest.MonkeyPatch,
):
    holding = await _single_holding(db_session, test_user)
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(PriceFetcher, "fetch_and_store_price", fetch)
    monkeypatch.setattr(
        portfolio_snapshot_worker, "async_session", _fake_session_factory(db_session)
    )

    await portfolio_snapshot_worker.tick()

    rows = await crud.portfolio_snapshot.get_range(
        db_session, owner_id=test_user.id, start_date=None
    )
    assert len(rows) == 1
    assert rows[0].snapshot_date == portfolio_today()
    assert rows[0].usd_mxn_rate == RATE
    assert [p.holding_id for p in rows[0].positions] == [holding.id]
    assert fetch.await_count >= 1
