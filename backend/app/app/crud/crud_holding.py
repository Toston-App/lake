from decimal import Decimal
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import select

from app import crud
from app.crud.base import CRUDBase
from app.models.asset import Asset, AssetClass, Currency
from app.models.holding import Holding
from app.schemas.holding import HoldingCreate, HoldingUpdate


def _convert_amount(
    amount: Decimal,
    from_currency: Currency,
    to_currency: Currency,
    usd_mxn_rate: Decimal,
) -> Decimal:
    if from_currency == to_currency:
        return amount
    if from_currency == Currency.USD:
        return amount * usd_mxn_rate
    return amount / usd_mxn_rate


class CRUDHolding(CRUDBase[Holding, HoldingCreate, HoldingUpdate]):
    async def remove_with_commit(
        self, db: AsyncSession, *, id: int, commit: bool = True
    ) -> Holding:
        obj = await db.get(self.model, id)
        if obj is None:
            raise ValueError("Holding not found")
        await db.delete(obj)
        if commit:
            await db.commit()
        else:
            await db.flush()
        return cast(Holding, obj)

    async def create_with_owner(
        self,
        db: AsyncSession,
        *,
        obj_in: HoldingCreate,
        owner_id: int,
        asset_currency: Currency,
        usd_mxn_rate: Decimal,
        commit: bool = True,
    ) -> Holding:
        """Create a new holding for a user."""
        obj_in_data = {
            "asset_id": obj_in.asset_id,
            "account_id": obj_in.account_id,
            "quantity": obj_in.quantity,
            "avg_cost_basis": obj_in.avg_cost_basis,
            "cost_currency": obj_in.cost_currency,
        }

        # Calculate total invested from quantity and cost basis
        obj_in_data["total_invested"] = (
            obj_in_data["quantity"] * obj_in_data["avg_cost_basis"]
        )
        native_value = _convert_amount(
            obj_in_data["total_invested"],
            obj_in_data["cost_currency"],
            asset_currency,
            usd_mxn_rate,
        )
        obj_in_data["current_value"] = native_value
        obj_in_data["current_value_usd"] = _convert_amount(
            native_value, asset_currency, Currency.USD, usd_mxn_rate
        )
        obj_in_data["current_value_mxn"] = _convert_amount(
            native_value, asset_currency, Currency.MXN, usd_mxn_rate
        )
        obj_in_data["unrealized_gain_loss"] = Decimal("0")
        obj_in_data["unrealized_gain_loss_pct"] = Decimal("0")

        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        if commit:
            await db.commit()
        else:
            await db.flush()
        await db.refresh(db_obj)

        # Recalculate account total_investments
        await crud.account.recalculate_total_investments(
            db, account_id=db_obj.account_id, commit=commit
        )

        return db_obj

    async def get_for_update_by_owner(
        self, db: AsyncSession, *, holding_id: int, owner_id: int
    ) -> Holding | None:
        """Owner-scoped lookup that locks the position for a financial mutation."""
        result = await db.execute(
            select(self.model)
            .options(selectinload(Holding.asset))
            .filter(Holding.id == holding_id, Holding.owner_id == owner_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalars().first()

    async def get_by_id_and_owner(
        self, db: AsyncSession, *, holding_id: int, owner_id: int
    ) -> Holding | None:
        result = await db.execute(
            select(self.model)
            .options(selectinload(Holding.asset))
            .filter(Holding.id == holding_id, Holding.owner_id == owner_id)
        )
        return result.scalars().first()

    async def exists_by_owner_and_asset(
        self, db: AsyncSession, *, owner_id: int, asset_id: int
    ) -> bool:
        result = await db.execute(
            select(Holding.id)
            .filter(Holding.owner_id == owner_id, Holding.asset_id == asset_id)
            .limit(1)
        )
        return result.scalar() is not None

    async def get_by_owner(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        skip: int = 0,
        limit: int | None = 100,
    ) -> list[Holding]:
        """Get all holdings for a user."""
        query = (
            select(self.model)
            .options(selectinload(Holding.asset))
            .filter(Holding.owner_id == owner_id)
            .offset(skip)
        )
        if limit is not None:
            query = query.limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    async def get_by_account_and_asset(
        self,
        db: AsyncSession,
        *,
        account_id: int,
        asset_id: int,
        owner_id: int | None = None,
    ) -> Holding | None:
        """Get a specific holding by account and asset."""
        query = (
            select(self.model)
            .options(selectinload(Holding.asset))
            .filter(Holding.account_id == account_id, Holding.asset_id == asset_id)
        )
        if owner_id is not None:
            query = query.filter(Holding.owner_id == owner_id)
        result = await db.execute(query)
        return result.scalars().first()

    async def get_by_account(
        self,
        db: AsyncSession,
        *,
        account_id: int,
        owner_id: int | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Holding]:
        """Get all holdings for a specific account."""
        query = (
            select(self.model)
            .options(selectinload(Holding.asset))
            .filter(Holding.account_id == account_id)
        )
        if owner_id is not None:
            query = query.filter(Holding.owner_id == owner_id)
        result = await db.execute(query.offset(skip).limit(limit))
        return result.scalars().all()

    async def get_by_asset_class(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        asset_class: AssetClass,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Holding]:
        """Get holdings filtered by asset class."""
        result = await db.execute(
            select(self.model)
            .join(Asset)
            .options(selectinload(Holding.asset))
            .filter(Holding.owner_id == owner_id, Asset.asset_class == asset_class)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_currency(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        currency: Currency,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Holding]:
        """Get holdings filtered by asset currency."""
        result = await db.execute(
            select(self.model)
            .join(Asset)
            .options(selectinload(Holding.asset))
            .filter(Holding.owner_id == owner_id, Asset.currency == currency)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_filtered(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        asset_class: AssetClass | None = None,
        currency: Currency | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Holding]:
        """Get holdings with optional filtering."""
        query = (
            select(self.model)
            .join(Asset)
            .options(selectinload(Holding.asset))
            .filter(Holding.owner_id == owner_id)
        )

        if asset_class is not None:
            query = query.filter(Asset.asset_class == asset_class)
        if currency is not None:
            query = query.filter(Asset.currency == currency)

        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    async def update_holding_value(
        self,
        db: AsyncSession,
        *,
        holding: Holding,
        current_price: Decimal,
        price_usd: Decimal,
        price_mxn: Decimal,
        commit: bool = True,
    ) -> Holding:
        """Update holding current value based on latest price."""
        # Calculate current values
        current_value = holding.quantity * current_price
        current_value_usd = holding.quantity * price_usd
        current_value_mxn = holding.quantity * price_mxn

        # Calculate unrealized gain/loss
        current_value_in_cost_currency = (
            current_value_usd
            if holding.cost_currency == Currency.USD
            else current_value_mxn
        )
        unrealized_gain_loss = current_value_in_cost_currency - holding.total_invested
        if holding.total_invested > 0:
            unrealized_gain_loss_pct = (
                unrealized_gain_loss / holding.total_invested
            ) * 100
        else:
            unrealized_gain_loss_pct = Decimal("0")

        holding.current_value = current_value
        holding.current_value_usd = current_value_usd
        holding.current_value_mxn = current_value_mxn
        holding.unrealized_gain_loss = unrealized_gain_loss
        holding.unrealized_gain_loss_pct = unrealized_gain_loss_pct

        db.add(holding)
        if commit:
            await db.commit()
        else:
            await db.flush()
        await db.refresh(holding)
        return holding

    async def recalculate_cost_basis(
        self,
        db: AsyncSession,
        *,
        holding: Holding,
        new_quantity: Decimal,
        new_total_invested: Decimal,
        usd_mxn_rate: Decimal,
        commit: bool = True,
    ) -> Holding:
        """Recalculate cost basis after a transaction."""
        if (
            not new_quantity.is_finite()
            or not new_total_invested.is_finite()
            or new_quantity < 0
            or new_quantity > 1e15
            or new_total_invested < 0
            or new_total_invested > 1e30
        ):
            raise ValueError("Unsafe holding value")
        old_quantity = holding.quantity
        if new_quantity > 0:
            holding.quantity = new_quantity
            holding.total_invested = new_total_invested
            holding.avg_cost_basis = new_total_invested / new_quantity
            if old_quantity > 0:
                quantity_ratio = new_quantity / old_quantity
                holding.current_value *= quantity_ratio
                holding.current_value_usd *= quantity_ratio
                holding.current_value_mxn *= quantity_ratio
            else:
                holding.current_value = _convert_amount(
                    new_total_invested,
                    holding.cost_currency,
                    holding.asset.currency,
                    usd_mxn_rate,
                )
                holding.current_value_usd = _convert_amount(
                    new_total_invested,
                    holding.cost_currency,
                    Currency.USD,
                    usd_mxn_rate,
                )
                holding.current_value_mxn = _convert_amount(
                    new_total_invested,
                    holding.cost_currency,
                    Currency.MXN,
                    usd_mxn_rate,
                )

            current_value_in_cost_currency = (
                holding.current_value_usd
                if holding.cost_currency == Currency.USD
                else holding.current_value_mxn
            )
            holding.unrealized_gain_loss = (
                current_value_in_cost_currency - new_total_invested
            )
            holding.unrealized_gain_loss_pct = (
                holding.unrealized_gain_loss / new_total_invested * 100
                if new_total_invested > 0
                else Decimal("0")
            )
        else:
            # All shares sold
            holding.quantity = Decimal("0")
            holding.total_invested = Decimal("0")
            holding.avg_cost_basis = Decimal("0")
            holding.current_value = Decimal("0")
            holding.current_value_usd = Decimal("0")
            holding.current_value_mxn = Decimal("0")
            holding.unrealized_gain_loss = Decimal("0")
            holding.unrealized_gain_loss_pct = Decimal("0")

        db.add(holding)
        if commit:
            await db.commit()
        else:
            await db.flush()
        await db.refresh(holding)

        # Recalculate account total_investments
        await crud.account.recalculate_total_investments(
            db, account_id=holding.account_id, commit=commit
        )

        return holding


holding = CRUDHolding(Holding)
