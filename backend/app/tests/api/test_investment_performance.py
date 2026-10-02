# ruff: noqa: ARG001
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.models.asset import AssetType, Market
from app.models.investment_transaction import TransactionType
from app.models.portfolio_snapshot import PortfolioSnapshot, PortfolioSnapshotHolding
from app.services.currency_converter import CurrencyConverter, CurrencyRateUnavailable
from app.services.portfolio_snapshot import portfolio_today
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_holding,
    create_test_investment_transaction,
    create_test_user,
)

PREFIX = "/api/v1/investments/portfolio"


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


async def _holding(db: AsyncSession, user):
    account = await create_test_account(db, owner_id=user.id, name="Brokerage")
    asset = await create_test_asset(db, symbol="PERF")
    holding = await create_test_holding(
        db,
        owner_id=user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal("10"),
        avg_cost_basis=Decimal("100"),
    )
    return account, holding


async def _snapshot(
    db: AsyncSession,
    user,
    *,
    days_ago: int,
    value_usd: Decimal = Decimal("1000"),
    positions: list[tuple[int, Decimal, Decimal]] = (),
) -> PortfolioSnapshot:
    snapshot = PortfolioSnapshot(
        owner_id=user.id,
        snapshot_date=portfolio_today() - timedelta(days=days_ago),
        captured_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        total_value_usd=value_usd,
        total_value_mxn=value_usd * 18,
        total_invested_usd=Decimal("1000"),
        total_invested_mxn=Decimal("0"),
        usd_mxn_rate=Decimal("18"),
        holdings_count=len(positions),
        positions=[
            PortfolioSnapshotHolding(
                holding_id=holding_id,
                asset_id=0,
                quantity=quantity,
                value_usd=value,
                value_mxn=value * 18,
            )
            for holding_id, quantity, value in positions
        ],
    )
    db.add(snapshot)
    await db.commit()
    return snapshot


async def test_empty_portfolio(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    response = await client.get(f"{PREFIX}/performance")
    assert response.status_code == 200
    data = response.json()
    assert data["period"] == "1M"
    assert data["currency"] == "USD"
    assert len(data["data_points"]) == 1
    assert data["start_value"] == data["end_value"] == 0
    assert data["absolute_return"] == 0
    assert data["percentage_return"] == 0
    assert data["net_contributions"] == 0


async def test_live_point_matches_summary(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    await _portfolio(db_session, test_user)
    summary = (await client.get(f"{PREFIX}/summary")).json()

    usd = (await client.get(f"{PREFIX}/performance")).json()
    assert usd["end_value"] == summary["total_value_usd"]
    assert usd["data_points"][-1]["invested"] == summary["total_invested_combined_usd"]

    mxn = (await client.get(f"{PREFIX}/performance", params={"currency": "MXN"})).json()
    assert mxn["currency"] == "MXN"
    assert mxn["end_value"] == summary["total_value_mxn"]


async def test_window_and_baseline(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    for days_ago in (40, 20, 5):
        await _snapshot(db_session, test_user, days_ago=days_ago)
    today = portfolio_today()

    month = (await client.get(f"{PREFIX}/performance", params={"period": "1M"})).json()
    assert len(month["data_points"]) == 4
    assert month["start_date"] == (today - timedelta(days=40)).isoformat()
    assert month["end_date"] == today.isoformat()

    week = (await client.get(f"{PREFIX}/performance", params={"period": "1W"})).json()
    assert len(week["data_points"]) == 3
    assert week["start_date"] == (today - timedelta(days=20)).isoformat()

    everything = (
        await client.get(f"{PREFIX}/performance", params={"period": "ALL"})
    ).json()
    assert len(everything["data_points"]) == 4


async def test_todays_snapshot_is_replaced_by_live_point(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    await _portfolio(db_session, test_user)
    await _snapshot(db_session, test_user, days_ago=0, value_usd=Decimal("9999"))

    data = (await client.get(f"{PREFIX}/performance")).json()
    assert len(data["data_points"]) == 1
    assert data["data_points"][0]["date"] == portfolio_today().isoformat()
    assert data["data_points"][0]["value"] == pytest.approx(400)
    assert data["end_value"] == pytest.approx(400)


async def test_market_gain_end_to_end(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    _, holding = await _holding(db_session, test_user)
    await _snapshot(
        db_session,
        test_user,
        days_ago=1,
        positions=[(holding.id, Decimal("10"), Decimal("1000"))],
    )
    holding.current_value = holding.current_value_usd = Decimal("1100")
    holding.current_value_mxn = Decimal("19800")
    db_session.add(holding)
    await db_session.commit()

    data = (await client.get(f"{PREFIX}/performance")).json()
    assert data["absolute_return"] == pytest.approx(100)
    assert data["percentage_return"] == pytest.approx(10)
    assert data["net_contributions"] == pytest.approx(0)


async def test_split_end_to_end(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account, holding = await _holding(db_session, test_user)
    await _snapshot(
        db_session,
        test_user,
        days_ago=1,
        positions=[(holding.id, Decimal("10"), Decimal("1000"))],
    )
    await create_test_investment_transaction(
        db_session,
        owner_id=test_user.id,
        account_id=account.id,
        holding_id=holding.id,
        transaction_type=TransactionType.SPLIT,
        quantity=Decimal("4"),
    )
    holding.quantity = Decimal("40")
    holding.current_value = holding.current_value_usd = Decimal("1000")
    holding.current_value_mxn = Decimal("18000")
    db_session.add(holding)
    await db_session.commit()

    data = (await client.get(f"{PREFIX}/performance")).json()
    assert data["absolute_return"] == pytest.approx(0)
    assert data["percentage_return"] == pytest.approx(0)


async def test_other_users_snapshots_are_isolated(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    other = await create_test_user(db_session, email="other-perf@example.com")
    for days_ago in (10, 3):
        await _snapshot(db_session, other, days_ago=days_ago)

    data = (await client.get(f"{PREFIX}/performance", params={"period": "ALL"})).json()
    assert len(data["data_points"]) == 1
    assert data["end_value"] == 0


async def test_invalid_period(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    response = await client.get(f"{PREFIX}/performance", params={"period": "2D"})
    assert response.status_code == 422


async def test_fx_unavailable(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user,
    enable_investments,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        CurrencyConverter,
        "get_usd_to_mxn_rate",
        AsyncMock(side_effect=CurrencyRateUnavailable("x")),
    )
    response = await client.get(f"{PREFIX}/performance")
    assert response.status_code == 503
