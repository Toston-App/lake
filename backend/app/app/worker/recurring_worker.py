"""Background worker that creates the transactions of recurring templates when due."""

from __future__ import annotations

import asyncio
import logging
import os

from app.db.session import async_session
from app.services.recurring import claim_due, generate_due, release_leases

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
    attempted = 0
    try:
        for recurring_id in ids:
            attempted += 1
            try:
                async with async_session() as db:
                    created += await generate_due(db, recurring_id, leased=True)
            except Exception:
                # Keep this template's lease so it is retried once it expires,
                # but carry on with the rest of the batch.
                logger.exception(
                    "Failed generating recurring transaction %s", recurring_id
                )
    finally:
        # Interrupted mid-batch (e.g. cancelled): hand back what was not reached.
        await _release(ids[attempted:])
    logger.info(
        "Processed %s recurring templates, created %s transactions", len(ids), created
    )
    return True


async def _release(ids: list[int]) -> None:
    if not ids:
        return
    try:
        async with async_session() as db:
            await release_leases(db, ids)
    except Exception:
        logger.exception("Failed releasing leases of recurring transactions %s", ids)


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
