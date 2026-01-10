from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.asset_price import AssetPrice
from app.schemas.asset_price import AssetPriceCreate, AssetPriceUpdate


class CRUDAssetPrice(CRUDBase[AssetPrice, AssetPriceCreate, AssetPriceUpdate]):
    async def get_latest_by_asset(
        self, db: AsyncSession, *, asset_id: int
    ) -> Optional[AssetPrice]:
        """Get the most recent price for an asset."""
        result = await db.execute(
            select(self.model)
            .filter(AssetPrice.asset_id == asset_id)
            .order_by(AssetPrice.fetched_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def get_latest_prices(
        self, db: AsyncSession, *, asset_ids: list[int]
    ) -> dict[int, AssetPrice]:
        """Get the most recent price for multiple assets."""
        # This is a simple approach; for large numbers of assets,
        # a more optimized query would be needed
        prices = {}
        for asset_id in asset_ids:
            price = await self.get_latest_by_asset(db, asset_id=asset_id)
            if price:
                prices[asset_id] = price
        return prices

    async def is_stale(
        self, db: AsyncSession, *, asset_id: int, max_age_minutes: int = 15
    ) -> bool:
        """Check if the price for an asset is stale (older than max_age_minutes)."""
        latest = await self.get_latest_by_asset(db, asset_id=asset_id)
        if not latest:
            return True
        
        cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
        return latest.fetched_at.replace(tzinfo=None) < cutoff

    async def get_history(
        self,
        db: AsyncSession,
        *,
        asset_id: int,
        start_date: datetime,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AssetPrice]:
        """Get historical prices for an asset."""
        query = select(self.model).filter(
            AssetPrice.asset_id == asset_id,
            AssetPrice.fetched_at >= start_date,
        )
        
        if end_date:
            query = query.filter(AssetPrice.fetched_at <= end_date)
        
        query = query.order_by(AssetPrice.fetched_at.desc()).limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    async def cleanup_old_prices(
        self, db: AsyncSession, *, days_to_keep: int = 30
    ) -> int:
        """Delete prices older than days_to_keep (keeping one per day)."""
        # This is a placeholder - in production, you'd want a more
        # sophisticated approach that keeps daily snapshots
        cutoff = datetime.utcnow() - timedelta(days=days_to_keep)
        
        # For now, just return 0 as this needs careful implementation
        return 0


asset_price = CRUDAssetPrice(AssetPrice)

