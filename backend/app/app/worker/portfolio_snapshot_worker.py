"""Background worker that refreshes held asset prices and snapshots every portfolio."""

from __future__ import annotations

import asyncio
import logging
import os

from app import crud
from app.core.config import settings
from app.db.session import async_session
from app.services.currency_converter import CurrencyConverter, CurrencyRateUnavailable
from app.services.portfolio_snapshot import (
    held_active_asset_ids,
    portfolio_today,
    snapshot_all_owners,
)
from app.services.price_fetcher import PriceFetcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("portfolio_snapshot_worker")


async def refresh_held_prices() -> None:
    async with async_session() as db:
        asset_ids = await held_active_asset_ids(db)

    for asset_id in asset_ids:
        async with async_session() as db:
            asset = await crud.asset.get(db, id=asset_id)
            if asset is None:
                continue
            if not await crud.asset_price.is_stale(
                db,
                asset_id=asset_id,
                max_age_minutes=settings.PORTFOLIO_SNAPSHOT_PRICE_MAX_AGE_MINUTES,
            ):
                continue
            try:
                price = await PriceFetcher.fetch_and_store_price(db, asset)
            except Exception:
                logger.exception("Price refresh failed for asset %s", asset_id)
                continue
            if price is None:
                logger.debug("Asset %s cannot be auto-priced", asset_id)


async def tick() -> None:
    if not settings.INVESTMENTS_ENABLED:
        logger.debug("Investments disabled; skipping portfolio snapshots")
        return

    await refresh_held_prices()

    try:
        rate = await CurrencyConverter.get_usd_to_mxn_rate()
    except CurrencyRateUnavailable:
        # A guessed rate would corrupt history; try again next interval.
        logger.warning("USD/MXN rate unavailable; skipping portfolio snapshots")
        return

    async with async_session() as db:
        count = await snapshot_all_owners(
            db, snapshot_date=portfolio_today(), usd_mxn_rate=rate
        )
        await db.commit()
    logger.info("Snapshotted %d portfolios", count)


async def run_forever() -> None:
    logger.info("Portfolio snapshot worker started")
    while True:
        try:
            await tick()
        except Exception:
            logger.exception("Portfolio snapshot worker loop error")
        await asyncio.sleep(settings.PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS)


def main() -> None:
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        logger.info("Portfolio snapshot worker stopped")
        os._exit(0)


if __name__ == "__main__":
    main()
