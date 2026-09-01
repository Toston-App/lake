from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_export import DataExport, DataExportStatus

INFLIGHT = (DataExportStatus.pending, DataExportStatus.processing)


class CRUDDataExport:
    async def get(self, db: AsyncSession, id: UUID) -> DataExport | None:
        result = await db.execute(select(DataExport).where(DataExport.id == id))
        return result.scalars().first()

    async def get_for_owner(
        self, db: AsyncSession, *, id: UUID, owner_id: int
    ) -> DataExport | None:
        result = await db.execute(
            select(DataExport).where(
                DataExport.id == id, DataExport.owner_id == owner_id
            )
        )
        return result.scalars().first()

    async def get_inflight(
        self, db: AsyncSession, *, owner_id: int
    ) -> DataExport | None:
        result = await db.execute(
            select(DataExport)
            .where(
                DataExport.owner_id == owner_id,
                DataExport.status.in_(INFLIGHT),
            )
            .order_by(DataExport.created_at.desc())
        )
        return result.scalars().first()

    async def get_latest_for_owner(
        self, db: AsyncSession, *, owner_id: int
    ) -> DataExport | None:
        result = await db.execute(
            select(DataExport)
            .where(DataExport.owner_id == owner_id)
            .order_by(DataExport.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def create_pending(
        self, db: AsyncSession, *, owner_id: int, filename: str
    ) -> DataExport:
        obj = DataExport(
            owner_id=owner_id,
            status=DataExportStatus.pending,
            progress_pct=0,
            filename=filename,
        )
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj

    async def expire_ready_for_owner(
        self, db: AsyncSession, *, owner_id: int
    ) -> list[DataExport]:
        result = await db.execute(
            select(DataExport).where(
                DataExport.owner_id == owner_id,
                DataExport.status == DataExportStatus.ready,
            )
        )
        rows = list(result.scalars().all())
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = DataExportStatus.expired
            row.updated_at = now
        if rows:
            await db.commit()
            for row in rows:
                await db.refresh(row)
        return rows

    async def claim_next_pending(self, db: AsyncSession) -> DataExport | None:
        result = await db.execute(
            select(DataExport)
            .where(DataExport.status == DataExportStatus.pending)
            .order_by(DataExport.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = result.scalars().first()
        if not job:
            return None
        now = datetime.now(timezone.utc)
        job.status = DataExportStatus.processing
        job.progress_step = "starting"
        job.heartbeat_at = now
        job.updated_at = now
        await db.commit()
        await db.refresh(job)
        return job

    async def mark_progress(
        self,
        db: AsyncSession,
        export: DataExport,
        *,
        pct: int,
        step: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        export.progress_pct = max(0, min(100, pct))
        export.progress_step = step
        export.heartbeat_at = now
        export.updated_at = now
        db.add(export)
        await db.commit()
        await db.refresh(export)

    async def mark_ready(
        self,
        db: AsyncSession,
        export: DataExport,
        *,
        object_key: str,
        content_sha256: str,
        byte_size: int,
        retention_days: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        export.status = DataExportStatus.ready
        export.progress_pct = 100
        export.progress_step = "done"
        export.object_key = object_key
        export.content_sha256 = content_sha256
        export.byte_size = byte_size
        export.error = None
        export.completed_at = now
        export.expires_at = now + timedelta(days=retention_days)
        export.heartbeat_at = now
        export.updated_at = now
        db.add(export)
        await db.commit()
        await db.refresh(export)

    async def mark_failed(
        self, db: AsyncSession, export: DataExport, *, error: str
    ) -> None:
        now = datetime.now(timezone.utc)
        export.status = DataExportStatus.failed
        export.error = error
        export.heartbeat_at = now
        export.updated_at = now
        db.add(export)
        await db.commit()
        await db.refresh(export)

    async def reap_stale_processing(
        self, db: AsyncSession, *, stale_after_seconds: int
    ) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        result = await db.execute(
            update(DataExport)
            .where(
                DataExport.status == DataExportStatus.processing,
                DataExport.heartbeat_at.isnot(None),
                DataExport.heartbeat_at < cutoff,
            )
            .values(
                status=DataExportStatus.failed,
                error="Export timed out. Please try again.",
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        return result.rowcount or 0

    async def list_expired_ready(self, db: AsyncSession) -> list[DataExport]:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(DataExport).where(
                DataExport.status == DataExportStatus.ready,
                DataExport.expires_at.isnot(None),
                DataExport.expires_at < now,
            )
        )
        return list(result.scalars().all())


data_export = CRUDDataExport()
