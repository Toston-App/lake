"""Background worker that creates the transactions of recurring templates when due."""

from __future__ import annotations

import asyncio
import logging
import os

from app.db.session import async_session
from app.services.recurring import claim_due, generate_due

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("recurring_worker")

IDLE_SLEEP_SECONDS = int(os.getenv("RECURRING_WORKER_INTERVAL_SECONDS", "300"))
ERROR_SLEEP_SECONDS = 30
BATCH_SIZE = 50


async def tick() -> bool:
    """Generate one batch of due templates; returns True when a batch was claimed."""
    async with async_session() as db:
        ids = await claim_due(db, limit=BATCH_SIZE)
    if not ids:
        return False

    created = 0
    for recurring_id in ids:
        async with async_session() as db:
            created += await generate_due(db, recurring_id, leased=True)
    logger.info(
        "Processed %s recurring templates, created %s transactions", len(ids), created
    )
    return True


async def run_forever() -> None:
    logger.info("Recurring worker started")
    while True:
        try:
            did_work = await tick()
            if not did_work:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
        except Exception:
            logger.exception("Recurring worker loop error")
            await asyncio.sleep(ERROR_SLEEP_SECONDS)


def main() -> None:
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        logger.info("Recurring worker stopped")
        os._exit(0)


if __name__ == "__main__":
    main()
