# ruff: noqa: ARG001
from decimal import Decimal

import pytest
from app.models.asset import AssetType, Market
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_holding,
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
    first.unrealized_gain_loss = Decimal("20")
    first.unrealized_gain_loss_pct = Decimal("25")
    second.current_value = second.current_value_usd = Decimal("300")
    second.current_value_mxn = Decimal("5400")
    second.unrealized_gain_loss = Decimal("250")
    second.unrealized_gain_loss_pct = Decimal("500")
    db.add_all([first, second])
    await db.commit()
    return first, second


async def test_summary_mixed_values_and_empty(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    empty = await client.get(f"{PREFIX}/summary")
    assert empty.status_code == 200
    assert empty.json()["total_holdings"] == 0
    await _portfolio(db_session, test_user)
    response = await client.get(f"{PREFIX}/summary")
    data = response.json()
    assert data["total_value_usd"] == pytest.approx(400)
    assert data["total_value_mxn"] == pytest.approx(7200)
    assert data["total_invested_combined_usd"] == pytest.approx(130)
    assert data["total_gain_loss"] == pytest.approx(270)
    assert data["total_gain_loss_pct"] == pytest.approx(207.69)
    assert data["total_holdings"] == data["total_assets"] == 2


@pytest.mark.parametrize(
    "endpoint,expected_values",
    [
        ("by-class", {"equities", "crypto"}),
        ("by-currency", {"USD"}),
        ("by-market", {"NASDAQ", "CRYPTO"}),
        ("by-type", {"stock", "cryptocurrency"}),
        ("by-country", {"US", "GLOBAL"}),
    ],
)
async def test_allocation_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user,
    enable_investments,
    endpoint,
    expected_values,
):
    await _portfolio(db_session, test_user)
    response = await client.get(f"{PREFIX}/allocation/{endpoint}")
    assert response.status_code == 200
    nonzero = {
        item["value"]
        for item in response.json()["allocations"]
        if item["holdings_count"]
    }
    assert nonzero == expected_values
    percentages = {
        item["value"]: item["percentage"] for item in response.json()["allocations"]
    }
    if "equities" in percentages:
        assert percentages["equities"] == 25.0
        assert percentages["crypto"] == 75.0


async def test_allocation_by_account_and_top_holdings(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    await _portfolio(db_session, test_user)
    accounts = await client.get(f"{PREFIX}/allocation/by-account")
    assert accounts.status_code == 200
    assert [item["name"] for item in accounts.json()["allocations"]] == [
        "Crypto",
        "Brokerage",
    ]
    top = await client.get(f"{PREFIX}/top-holdings?limit=1")
    assert top.status_code == 200
    assert top.json()["holdings"][0]["symbol"] == "BTC"
    assert top.json()["holdings"][0]["percentage_of_portfolio"] == 75.0
    assert top.json()["total_holdings"] == 2
    assert (await client.get(f"{PREFIX}/top-holdings?limit=0")).status_code == 422
    assert (await client.get(f"{PREFIX}/top-holdings?limit=51")).status_code == 422


async def test_portfolio_is_cross_user_isolated(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    other = await create_test_user(db_session, email="portfolio-other@example.com")
    await _portfolio(db_session, other)
    response = await client.get(f"{PREFIX}/summary")
    assert response.json()["total_holdings"] == 0
