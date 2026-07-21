from datetime import datetime, timezone
from decimal import Decimal

import pytest
from app.api.api_v1.endpoints.investment_transactions import (
    _update_holding_from_transaction,
)
from app.models.investment_transaction import TransactionType
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_holding,
    create_test_investment_transaction,
    create_test_user,
)


async def _position(db: AsyncSession, symbol: str):
    user = await create_test_user(db, email=f"{symbol.lower()}-math@example.com")
    account = await create_test_account(db, owner_id=user.id)
    asset = await create_test_asset(db, symbol=symbol)
    holding = await create_test_holding(
        db,
        owner_id=user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal("10"),
        avg_cost_basis=Decimal("10"),
    )
    return user, account, holding


async def _apply(db, user, account, holding, kind, quantity, price, fees=Decimal("0")):
    tx = await create_test_investment_transaction(
        db,
        owner_id=user.id,
        account_id=account.id,
        holding_id=holding.id,
        transaction_type=kind,
        quantity=quantity,
        price_per_unit=price,
        fees=fees,
        executed_at=datetime.now(timezone.utc),
    )
    await _update_holding_from_transaction(db, holding, tx, usd_mxn_rate=Decimal("18"))


async def test_buy_buy_sell_chain(db_session: AsyncSession):
    user, account, holding = await _position(db_session, "CHAIN")
    await _apply(
        db_session,
        user,
        account,
        holding,
        TransactionType.BUY,
        Decimal("10"),
        Decimal("20"),
    )
    await _apply(
        db_session,
        user,
        account,
        holding,
        TransactionType.BUY,
        Decimal("5"),
        Decimal("30"),
        Decimal("5"),
    )
    await _apply(
        db_session,
        user,
        account,
        holding,
        TransactionType.SELL,
        Decimal("5"),
        Decimal("40"),
    )
    assert holding.quantity == Decimal("20")
    assert float(holding.total_invested) == pytest.approx(364)
    assert float(holding.avg_cost_basis) == pytest.approx(18.2)
    await db_session.refresh(account)
    assert account.total_investments_usd == holding.current_value_usd


async def test_buy_split_sell_chain(db_session: AsyncSession):
    user, account, holding = await _position(db_session, "SPLITCHAIN")
    await _apply(
        db_session,
        user,
        account,
        holding,
        TransactionType.BUY,
        Decimal("10"),
        Decimal("10"),
    )
    await _apply(
        db_session,
        user,
        account,
        holding,
        TransactionType.SPLIT,
        Decimal("2"),
        Decimal("0"),
    )
    await _apply(
        db_session,
        user,
        account,
        holding,
        TransactionType.SELL,
        Decimal("10"),
        Decimal("15"),
    )
    assert holding.quantity == Decimal("30")
    assert holding.total_invested == Decimal("150")
    assert holding.avg_cost_basis == Decimal("5")


async def test_transfer_in_out_chain(db_session: AsyncSession):
    user, account, holding = await _position(db_session, "TRANSFERCHAIN")
    await _apply(
        db_session,
        user,
        account,
        holding,
        TransactionType.TRANSFER_IN,
        Decimal("5"),
        Decimal("12"),
    )
    await _apply(
        db_session,
        user,
        account,
        holding,
        TransactionType.TRANSFER_OUT,
        Decimal("3"),
        Decimal("0"),
    )
    assert holding.quantity == Decimal("12")
    assert float(holding.total_invested) == pytest.approx(128)
    assert float(holding.avg_cost_basis) == pytest.approx(10.6666666667)
