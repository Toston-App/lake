from decimal import Decimal

import pytest
from app.models.asset import Currency
from app.schemas.holding import HoldingCreate
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_holding,
    create_test_user,
)


async def _position(db: AsyncSession, *, symbol: str = "HOLD"):
    user = await create_test_user(db, email=f"{symbol.lower()}@example.com")
    account = await create_test_account(db, owner_id=user.id)
    asset = await create_test_asset(db, symbol=symbol)
    return user, account, asset


async def test_create_with_owner_calculates_values_and_account_total(
    db_session: AsyncSession,
):
    user, account, asset = await _position(db_session)
    holding = await crud.holding.create_with_owner(
        db_session,
        obj_in=HoldingCreate(
            account_id=account.id,
            asset_id=asset.id,
            quantity=Decimal("2"),
            avg_cost_basis=Decimal("50"),
            cost_currency=Currency.USD,
        ),
        owner_id=user.id,
        asset_currency=Currency.USD,
        usd_mxn_rate=Decimal("18"),
    )
    assert float(holding.total_invested) == pytest.approx(100)
    assert float(holding.current_value_usd) == pytest.approx(100)
    assert float(holding.current_value_mxn) == pytest.approx(1800)
    await db_session.refresh(account)
    assert float(account.total_investments_usd) == pytest.approx(100)


async def test_holding_lookup_helpers_are_owner_scoped(db_session: AsyncSession):
    user, account, asset = await _position(db_session, symbol="LOOK")
    holding = await create_test_holding(
        db_session, owner_id=user.id, account_id=account.id, asset_id=asset.id
    )
    assert await crud.holding.exists_by_owner_and_asset(
        db_session, owner_id=user.id, asset_id=asset.id
    )
    assert not await crud.holding.exists_by_owner_and_asset(
        db_session, owner_id=user.id + 99, asset_id=asset.id
    )
    assert (
        await crud.holding.get_by_account_and_asset(
            db_session, account_id=account.id, asset_id=asset.id, owner_id=user.id
        )
    ).id == holding.id
    assert (
        await crud.holding.get_by_account_and_asset(
            db_session, account_id=account.id, asset_id=asset.id, owner_id=user.id + 99
        )
        is None
    )


async def test_update_holding_value_calculates_gain_loss(db_session: AsyncSession):
    user, account, asset = await _position(db_session, symbol="VALUE")
    holding = await create_test_holding(
        db_session,
        owner_id=user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal("10"),
        avg_cost_basis=Decimal("8"),
    )
    updated = await crud.holding.update_holding_value(
        db_session,
        holding=holding,
        current_price=Decimal("10"),
        price_usd=Decimal("10"),
        price_mxn=Decimal("180"),
    )
    assert float(updated.current_value_usd) == pytest.approx(100)
    assert float(updated.unrealized_gain_loss) == pytest.approx(20)
    assert float(updated.unrealized_gain_loss_pct) == pytest.approx(25)


async def test_recalculate_cost_basis_scales_and_zeroes(db_session: AsyncSession):
    user, account, asset = await _position(db_session, symbol="RECALC")
    holding = await create_test_holding(
        db_session,
        owner_id=user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal("10"),
        avg_cost_basis=Decimal("10"),
    )
    increased = await crud.holding.recalculate_cost_basis(
        db_session,
        holding=holding,
        new_quantity=Decimal("20"),
        new_total_invested=Decimal("300"),
        usd_mxn_rate=Decimal("18"),
    )
    assert float(increased.avg_cost_basis) == pytest.approx(15)
    assert float(increased.current_value_usd) == pytest.approx(200)
    zero = await crud.holding.recalculate_cost_basis(
        db_session,
        holding=holding,
        new_quantity=Decimal("0"),
        new_total_invested=Decimal("0"),
        usd_mxn_rate=Decimal("18"),
    )
    assert zero.quantity == zero.total_invested == zero.current_value_usd == 0


@pytest.mark.parametrize(
    "quantity,total",
    [
        (Decimal("-1"), Decimal("0")),
        (Decimal("1e16"), Decimal("0")),
        (Decimal("1"), Decimal("-1")),
        (Decimal("1"), Decimal("1e31")),
    ],
)
async def test_recalculate_cost_basis_rejects_unsafe_values(
    db_session: AsyncSession, quantity, total
):
    user, account, asset = await _position(
        db_session, symbol=f"BAD{abs(hash((quantity, total))) % 10000}"
    )
    holding = await create_test_holding(
        db_session, owner_id=user.id, account_id=account.id, asset_id=asset.id
    )
    with pytest.raises(ValueError, match="Unsafe"):
        await crud.holding.recalculate_cost_basis(
            db_session,
            holding=holding,
            new_quantity=quantity,
            new_total_invested=total,
            usd_mxn_rate=Decimal("18"),
        )


async def test_remove_with_commit_and_missing_guard(db_session: AsyncSession):
    user, account, asset = await _position(db_session, symbol="REMOVE")
    holding = await create_test_holding(
        db_session, owner_id=user.id, account_id=account.id, asset_id=asset.id
    )
    removed = await crud.holding.remove_with_commit(db_session, id=holding.id)
    assert removed.id == holding.id
    assert await crud.holding.get(db_session, id=holding.id) is None
    with pytest.raises(ValueError, match="not found"):
        await crud.holding.remove_with_commit(db_session, id=999999)
