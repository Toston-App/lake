from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from app.models.investment_transaction import TransactionType
from app.schemas.investment_transaction import InvestmentTransactionCreate
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_holding,
    create_test_investment_transaction,
    create_test_user,
)


async def _position(db: AsyncSession):
    user = await create_test_user(db, email="tx-crud@example.com")
    account = await create_test_account(db, owner_id=user.id)
    asset = await create_test_asset(db, symbol="TXCRUD")
    holding = await create_test_holding(
        db, owner_id=user.id, account_id=account.id, asset_id=asset.id
    )
    return user, account, holding


@pytest.mark.parametrize(
    "kind,expected",
    [
        (TransactionType.BUY, Decimal("20")),
        (TransactionType.SELL, Decimal("18")),
        (TransactionType.DIVIDEND, Decimal("20")),
    ],
)
async def test_create_with_owner_total_math(db_session: AsyncSession, kind, expected):
    user, account, holding = await _position(db_session)
    transaction = await crud.investment_transaction.create_with_owner(
        db_session,
        obj_in=InvestmentTransactionCreate(
            holding_id=holding.id,
            account_id=account.id,
            transaction_type=kind,
            quantity=Decimal("2"),
            price_per_unit=Decimal("10"),
            fees=Decimal("2"),
            executed_at=datetime.now(timezone.utc),
        ),
        owner_id=user.id,
        idempotency_key=f"key-{kind.value}",
        request_fingerprint="fingerprint",
    )
    assert transaction.total_amount == expected
    assert transaction.idempotency_key == f"key-{kind.value}"
    assert transaction.request_fingerprint == "fingerprint"


async def test_readers_and_fee_total(db_session: AsyncSession):
    user, account, holding = await _position(db_session)
    now = datetime.now(timezone.utc)
    buy = await create_test_investment_transaction(
        db_session,
        owner_id=user.id,
        account_id=account.id,
        holding_id=holding.id,
        transaction_type=TransactionType.BUY,
        fees=Decimal("1"),
        executed_at=now - timedelta(days=1),
    )
    sell = await create_test_investment_transaction(
        db_session,
        owner_id=user.id,
        account_id=account.id,
        holding_id=holding.id,
        transaction_type=TransactionType.SELL,
        fees=Decimal("2"),
        executed_at=now,
    )
    assert [
        t.id
        for t in await crud.investment_transaction.get_by_owner(
            db_session, owner_id=user.id
        )
    ] == [sell.id, buy.id]
    assert [
        t.id
        for t in await crud.investment_transaction.get_by_holding(
            db_session, holding_id=holding.id, owner_id=user.id
        )
    ] == [sell.id, buy.id]
    assert [
        t.id
        for t in await crud.investment_transaction.get_by_account(
            db_session, account_id=account.id, owner_id=user.id
        )
    ] == [sell.id, buy.id]
    assert [
        t.id
        for t in await crud.investment_transaction.get_by_type(
            db_session, owner_id=user.id, transaction_type=TransactionType.SELL
        )
    ] == [sell.id]
    ranged = await crud.investment_transaction.get_by_date_range(
        db_session,
        owner_id=user.id,
        start_date=now - timedelta(hours=1),
        end_date=now + timedelta(hours=1),
    )
    assert [t.id for t in ranged] == [sell.id]
    assert float(
        await crud.investment_transaction.get_total_fees(db_session, owner_id=user.id)
    ) == pytest.approx(3)
    assert (
        await crud.investment_transaction.get_by_idempotency_key(
            db_session, owner_id=user.id, idempotency_key=sell.idempotency_key
        )
    ).id == sell.id
