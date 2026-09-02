"""Background worker that builds user data exports and uploads them to R2."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from app.core.config import settings
from app.crud.crud_data_export import data_export as crud_export
from app.db.session import async_session
from app.models.data_export import DataExportStatus
from app.services import r2
from app.services.user_export import GENERIC_FAIL, process_export_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("export_worker")

IDLE_SLEEP_SECONDS = 2
ERROR_SLEEP_SECONDS = 5


async def expire_ready_jobs() -> None:
    async with async_session() as db:
        jobs = await crud_export.list_expired_ready(db)
        for job in jobs:
            if job.object_key:
                try:
                    await r2.delete_object(job.object_key)
                except Exception:
                    logger.exception("Failed deleting expired export %s", job.id)
            job.status = DataExportStatus.expired
            job.object_key = None
            await db.commit()


async def tick() -> bool:
    async with async_session() as db:
        await crud_export.reap_stale_processing(
            db, stale_after_seconds=settings.EXPORT_STALE_HEARTBEAT_SECONDS
        )

    await expire_ready_jobs()

    async with async_session() as db:
        job = await crud_export.claim_next_pending(db)
        if job is None:
            return False
        job_id = job.id

    try:
        async with async_session() as db:
            await process_export_job(db, job_id)
    except Exception:
        logger.exception("Unhandled error in export job %s", job_id)
        async with async_session() as db:
            job = await crud_export.get(db, job_id)
            if job is not None and job.status == DataExportStatus.processing:
                await crud_export.mark_failed(db, job, error=GENERIC_FAIL)
    return True


async def run_forever() -> None:
    logger.info("Export worker started")
    while True:
        try:
            did_work = await tick()
            if not did_work:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
        except Exception:
            logger.exception("Export worker loop error")
            await asyncio.sleep(ERROR_SLEEP_SECONDS)


def main() -> None:
    if not r2.is_configured():
        logger.error("R2 is not configured; refusing to start export worker")
        sys.exit(1)
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        logger.info("Export worker stopped")
        os._exit(0)


if __name__ == "__main__":
    main()
