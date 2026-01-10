from typing import Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.asset import Asset, AssetClass, AssetType, Currency, Market
from app.schemas.asset import AssetCreate, AssetUpdate


class CRUDAsset(CRUDBase[Asset, AssetCreate, AssetUpdate]):
    async def get_by_symbol(self, db: AsyncSession, *, symbol: str) -> Optional[Asset]:
        """Get an asset by its symbol."""
        result = await db.execute(
            select(self.model).filter(Asset.symbol == symbol.upper())
        )
        return result.scalars().first()

    async def get_multi_filtered(
        self,
        db: AsyncSession,
        *,
        asset_class: Optional[AssetClass] = None,
        asset_type: Optional[AssetType] = None,
        currency: Optional[Currency] = None,
        market: Optional[Market] = None,
        is_active: Optional[bool] = True,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Asset]:
        """Get assets with optional filtering by class, type, currency, and market."""
        query = select(self.model)
        
        filters = []
        if asset_class is not None:
            filters.append(Asset.asset_class == asset_class)
        if asset_type is not None:
            filters.append(Asset.asset_type == asset_type)
        if currency is not None:
            filters.append(Asset.currency == currency)
        if market is not None:
            filters.append(Asset.market == market)
        if is_active is not None:
            filters.append(Asset.is_active == is_active)
        
        if filters:
            query = query.filter(and_(*filters))
        
        query = query.order_by(Asset.symbol).offset(skip).limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    async def filter_by_class(
        self,
        db: AsyncSession,
        *,
        asset_class: AssetClass,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Asset]:
        """Get all assets of a specific asset class."""
        result = await db.execute(
            select(self.model)
            .filter(Asset.asset_class == asset_class, Asset.is_active == True)
            .order_by(Asset.symbol)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def filter_by_currency(
        self,
        db: AsyncSession,
        *,
        currency: Currency,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Asset]:
        """Get all assets denominated in a specific currency."""
        result = await db.execute(
            select(self.model)
            .filter(Asset.currency == currency, Asset.is_active == True)
            .order_by(Asset.symbol)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def search_assets(
        self,
        db: AsyncSession,
        *,
        query: str,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Asset]:
        """Search assets by symbol or name."""
        search_term = f"%{query.upper()}%"
        result = await db.execute(
            select(self.model)
            .filter(
                Asset.is_active == True,
                (Asset.symbol.ilike(search_term) | Asset.name.ilike(search_term))
            )
            .order_by(Asset.symbol)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def create(self, db: AsyncSession, *, obj_in: AssetCreate) -> Asset:
        """Create a new asset with symbol validation."""
        obj_in_data = jsonable_encoder(obj_in)
        # Ensure symbol is uppercase
        obj_in_data["symbol"] = obj_in_data["symbol"].upper()
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_or_create(
        self, db: AsyncSession, *, obj_in: AssetCreate
    ) -> tuple[Asset, bool]:
        """Get existing asset or create new one. Returns (asset, created)."""
        existing = await self.get_by_symbol(db, symbol=obj_in.symbol)
        if existing:
            return existing, False
        created = await self.create(db, obj_in=obj_in)
        return created, True


asset = CRUDAsset(Asset)

