from decimal import Decimal

import pytest
from app.models.asset import Currency, Market
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_asset_price,
    create_test_holding,
    create_test_user,
)


async def test_price_refresh_propagates_gain_and_account_totals(
    db_session: AsyncSession,
):
    user = await create_test_user(db_session, email="valuation@example.com")
    account = await create_test_account(db_session, owner_id=user.id)
    asset = await create_test_asset(db_session, symbol="VALUATION")
    holding = await create_test_holding(
        db_session,
        owner_id=user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal("4"),
        avg_cost_basis=Decimal("80"),
    )
    price = await create_test_asset_price(
        db_session,
        asset_id=asset.id,
        price=Decimal("100"),
        price_usd=Decimal("100"),
        price_mxn=Decimal("1800"),
    )
    await crud.holding.update_holding_value(
        db_session,
        holding=holding,
        current_price=price.price,
        price_usd=price.price_usd,
        price_mxn=price.price_mxn,
    )
    await crud.account.recalculate_total_investments(db_session, account_id=account.id)
    assert holding.current_value_usd == Decimal("400")
    assert holding.unrealized_gain_loss == Decimal("80")
    assert holding.unrealized_gain_loss_pct == Decimal("25")
    await db_session.refresh(account)
    assert account.total_investments_usd == Decimal("400")
    assert account.total_investments_mxn == Decimal("7200")


async def test_mixed_currency_portfolio_totals(db_session: AsyncSession):
    user = await create_test_user(db_session, email="mixed-valuation@example.com")
    account = await create_test_account(db_session, owner_id=user.id)
    usd = await create_test_asset(db_session, symbol="MIXUSD")
    mxn = await create_test_asset(
        db_session, symbol="MIXMXN", currency=Currency.MXN, market=Market.BMV
    )
    await create_test_holding(
        db_session,
        owner_id=user.id,
        account_id=account.id,
        asset_id=usd.id,
        quantity=Decimal("2"),
        avg_cost_basis=Decimal("100"),
    )
    await create_test_holding(
        db_session,
        owner_id=user.id,
        account_id=account.id,
        asset_id=mxn.id,
        quantity=Decimal("1"),
        avg_cost_basis=Decimal("1800"),
        cost_currency=Currency.MXN,
        asset_currency=Currency.MXN,
    )
    await crud.account.recalculate_total_investments(db_session, account_id=account.id)
    await db_session.refresh(account)
    assert float(account.total_investments_usd) == pytest.approx(300)
    assert float(account.total_investments_mxn) == pytest.approx(5400)
