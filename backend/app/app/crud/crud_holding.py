from typing import Optional

from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.asset import Asset, AssetClass, Currency
from app.models.holding import Holding
from app.schemas.holding import HoldingCreate, HoldingUpdate


class CRUDHolding(CRUDBase[Holding, HoldingCreate, HoldingUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: HoldingCreate, owner_id: int
    ) -> Holding:
        """Create a new holding for a user."""
        obj_in_data = {
            "asset_id": obj_in.asset_id,
            "quantity": obj_in.quantity,
            "avg_cost_basis": obj_in.avg_cost_basis,
            "cost_currency": obj_in.cost_currency,
        }
        
        # Calculate total invested from quantity and cost basis
        obj_in_data["total_invested"] = obj_in_data["quantity"] * obj_in_data["avg_cost_basis"]
        
        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Holding]:
        """Get all holdings for a user."""
        result = await db.execute(
            select(self.model)
            .options(selectinload(Holding.asset))
            .filter(Holding.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_owner_and_asset(
        self, db: AsyncSession, *, owner_id: int, asset_id: int
    ) -> Optional[Holding]:
        """Get a specific holding by owner and asset."""
        result = await db.execute(
            select(self.model)
            .options(selectinload(Holding.asset))
            .filter(Holding.owner_id == owner_id, Holding.asset_id == asset_id)
        )
        return result.scalars().first()

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
            .filter(
                Holding.owner_id == owner_id,
                Asset.asset_class == asset_class
            )
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
            .filter(
                Holding.owner_id == owner_id,
                Asset.currency == currency
            )
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_filtered(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        asset_class: Optional[AssetClass] = None,
        currency: Optional[Currency] = None,
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
        current_price: float,
        price_usd: float,
        price_mxn: float,
    ) -> Holding:
        """Update holding current value based on latest price."""
        # Calculate current values
        current_value = holding.quantity * current_price
        current_value_usd = holding.quantity * price_usd
        current_value_mxn = holding.quantity * price_mxn
        
        # Calculate unrealized gain/loss
        unrealized_gain_loss = current_value - holding.total_invested
        if holding.total_invested > 0:
            unrealized_gain_loss_pct = (unrealized_gain_loss / holding.total_invested) * 100
        else:
            unrealized_gain_loss_pct = 0.0
        
        holding.current_value = current_value
        holding.current_value_usd = current_value_usd
        holding.current_value_mxn = current_value_mxn
        holding.unrealized_gain_loss = unrealized_gain_loss
        holding.unrealized_gain_loss_pct = unrealized_gain_loss_pct
        
        db.add(holding)
        await db.commit()
        await db.refresh(holding)
        return holding

    async def recalculate_cost_basis(
        self,
        db: AsyncSession,
        *,
        holding: Holding,
        new_quantity: float,
        new_total_invested: float,
    ) -> Holding:
        """Recalculate cost basis after a transaction."""
        if new_quantity > 0:
            holding.quantity = new_quantity
            holding.total_invested = new_total_invested
            holding.avg_cost_basis = new_total_invested / new_quantity
        else:
            # All shares sold
            holding.quantity = 0
            holding.total_invested = 0
            holding.avg_cost_basis = 0
        
        db.add(holding)
        await db.commit()
        await db.refresh(holding)
        return holding


holding = CRUDHolding(Holding)
