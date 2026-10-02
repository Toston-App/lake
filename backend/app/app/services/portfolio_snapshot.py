"""Daily portfolio snapshots: totals plus per-holding positions per user per day."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.core.config import settings
from app.models.asset import Asset, Currency
from app.models.holding import Holding

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


@dataclass(frozen=True)
class PositionValue:
    holding_id: int
    asset_id: int
    quantity: Decimal
    value_usd: Decimal
    value_mxn: Decimal


@dataclass(frozen=True)
class PortfolioTotals:
    total_value_usd: Decimal
    total_value_mxn: Decimal
    total_invested_usd: Decimal  # raw, USD-cost holdings only
    total_invested_mxn: Decimal  # raw, MXN-cost holdings only
    total_invested_combined_usd: Decimal  # converted grand total
    total_invested_combined_mxn: Decimal  # converted grand total
    holdings_count: int
    positions: tuple[PositionValue, ...]


def compute_portfolio_totals(
    holdings: Iterable[Holding], usd_mxn_rate: Decimal
) -> PortfolioTotals:
    """Same math as GET /portfolio/summary, unrounded, plus per-holding positions."""
    total_value_usd = ZERO
    total_value_mxn = ZERO
    total_invested_usd = ZERO
    total_invested_mxn = ZERO
    holdings_count = 0
    positions: list[PositionValue] = []

    for holding in holdings:
        holdings_count += 1
        total_value_usd += holding.current_value_usd
        total_value_mxn += holding.current_value_mxn
        if holding.cost_currency == Currency.USD:
            total_invested_usd += holding.total_invested
        else:
            total_invested_mxn += holding.total_invested
        if holding.quantity > 0:
            positions.append(
                PositionValue(
                    holding_id=holding.id,
                    asset_id=holding.asset_id,
                    quantity=holding.quantity,
                    value_usd=holding.current_value_usd,
                    value_mxn=holding.current_value_mxn,
                )
            )

    return PortfolioTotals(
        total_value_usd=total_value_usd,
        total_value_mxn=total_value_mxn,
        total_invested_usd=total_invested_usd,
        total_invested_mxn=total_invested_mxn,
        total_invested_combined_usd=combined_invested(
            total_invested_usd, total_invested_mxn, usd_mxn_rate, Currency.USD
        ),
        total_invested_combined_mxn=combined_invested(
            total_invested_usd, total_invested_mxn, usd_mxn_rate, Currency.MXN
        ),
        holdings_count=holdings_count,
        positions=tuple(positions),
    )


def combined_invested(
    total_invested_usd: Decimal,
    total_invested_mxn: Decimal,
    usd_mxn_rate: Decimal,
    currency: Currency,
) -> Decimal:
    """Combined cost basis in `currency`, using the summary's conversion."""
    if currency == Currency.USD:
        return total_invested_usd + (total_invested_mxn / usd_mxn_rate)
    return (total_invested_usd * usd_mxn_rate) + total_invested_mxn


def portfolio_today() -> date:
    """Calendar date in PORTFOLIO_SNAPSHOT_TIMEZONE; the key for snapshot rows."""
    return datetime.now(ZoneInfo(settings.PORTFOLIO_SNAPSHOT_TIMEZONE)).date()


async def snapshot_owner(
    db: AsyncSession, *, owner_id: int, snapshot_date: date, usd_mxn_rate: Decimal
) -> PortfolioTotals:
    """Load the owner's holdings and upsert today's snapshot. Does not commit."""
    holdings = await crud.holding.get_by_owner(db, owner_id=owner_id, limit=None)
    totals = compute_portfolio_totals(holdings, usd_mxn_rate)
    await crud.portfolio_snapshot.upsert(
        db,
        owner_id=owner_id,
        snapshot_date=snapshot_date,
        totals=totals,
        usd_mxn_rate=usd_mxn_rate,
    )
    return totals


async def snapshot_all_owners(
    db: AsyncSession, *, snapshot_date: date, usd_mxn_rate: Decimal
) -> int:
    """Snapshot every owner with at least one holding; returns successes. Does not commit."""
    owner_ids = (await db.execute(select(Holding.owner_id).distinct())).scalars().all()
    count = 0
    for owner_id in owner_ids:
        try:
            async with db.begin_nested():
                await snapshot_owner(
                    db,
                    owner_id=owner_id,
                    snapshot_date=snapshot_date,
                    usd_mxn_rate=usd_mxn_rate,
                )
            count += 1
        except Exception:
            logger.exception("Portfolio snapshot failed for owner %s", owner_id)
    return count


async def held_active_asset_ids(db: AsyncSession) -> list[int]:
    """Distinct asset ids with Holding.quantity > 0 and Asset.is_active true."""
    result = await db.execute(
        select(Holding.asset_id)
        .join(Asset, Asset.id == Holding.asset_id)
        .where(Holding.quantity > 0, Asset.is_active.is_(True))
        .distinct()
        .order_by(Holding.asset_id)
    )
    return list(result.scalars().all())
