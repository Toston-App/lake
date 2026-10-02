from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import func

from app.models.portfolio_snapshot import PortfolioSnapshot, PortfolioSnapshotHolding

if TYPE_CHECKING:
    from app.services.portfolio_snapshot import PortfolioTotals


class CRUDPortfolioSnapshot:
    async def upsert(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        snapshot_date: date,
        totals: PortfolioTotals,
        usd_mxn_rate: Decimal,
    ) -> int:
        """Insert or replace the owner's snapshot for the day. Does not commit."""
        values = {
            "total_value_usd": totals.total_value_usd,
            "total_value_mxn": totals.total_value_mxn,
            "total_invested_usd": totals.total_invested_usd,
            "total_invested_mxn": totals.total_invested_mxn,
            "usd_mxn_rate": usd_mxn_rate,
            "holdings_count": totals.holdings_count,
        }
        stmt = (
            insert(PortfolioSnapshot)
            .values(
                owner_id=owner_id,
                snapshot_date=snapshot_date,
                captured_at=func.now(),
                **values,
            )
            .on_conflict_do_update(
                constraint="uq_portfolio_snapshot_owner_date",
                # ORM onupdate does not fire on ON CONFLICT; set updated_at here.
                set_={**values, "captured_at": func.now(), "updated_at": func.now()},
            )
            .returning(PortfolioSnapshot.id)
        )
        snapshot_id = (await db.execute(stmt)).scalar_one()

        await db.execute(
            delete(PortfolioSnapshotHolding).where(
                PortfolioSnapshotHolding.snapshot_id == snapshot_id
            )
        )
        if totals.positions:
            await db.execute(
                insert(PortfolioSnapshotHolding),
                [
                    {
                        "snapshot_id": snapshot_id,
                        "holding_id": position.holding_id,
                        "asset_id": position.asset_id,
                        "quantity": position.quantity,
                        "value_usd": position.value_usd,
                        "value_mxn": position.value_mxn,
                    }
                    for position in totals.positions
                ],
            )
        return snapshot_id

    async def get_range(
        self, db: AsyncSession, *, owner_id: int, start_date: date | None
    ) -> list[PortfolioSnapshot]:
        query = select(PortfolioSnapshot).where(PortfolioSnapshot.owner_id == owner_id)
        if start_date is not None:
            query = query.where(PortfolioSnapshot.snapshot_date >= start_date)
        result = await db.execute(
            query.order_by(PortfolioSnapshot.snapshot_date.asc())
            .options(selectinload(PortfolioSnapshot.positions))
            # The Core upsert bypasses the identity map; refresh loaded objects.
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def get_latest_on_or_before(
        self, db: AsyncSession, *, owner_id: int, day: date
    ) -> PortfolioSnapshot | None:
        result = await db.execute(
            select(PortfolioSnapshot)
            .where(
                PortfolioSnapshot.owner_id == owner_id,
                PortfolioSnapshot.snapshot_date <= day,
            )
            .order_by(PortfolioSnapshot.snapshot_date.desc())
            .limit(1)
            .options(selectinload(PortfolioSnapshot.positions))
            .execution_options(populate_existing=True)
        )
        return result.scalars().first()


portfolio_snapshot = CRUDPortfolioSnapshot()
