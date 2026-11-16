"""The user's data export: an Excel workbook built by the export worker.

Shared by ``/api/v1/exports`` and the chat/MCP export tools. Starting an
export queues a job (``export_worker`` builds the file and uploads it to R2);
a ready export is downloaded through a short-lived presigned URL.
"""

from __future__ import annotations

import contextlib
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.core.config import settings
from app.schemas.data_export import DataExportDownloadResponse, DataExportStatusResponse
from app.services import r2
from app.services.errors import ConflictError, NotFoundError, ServiceError
from app.services.user_export import export_filename


class ExportStorageUnavailable(ServiceError):
    status_code = 503
    reason = "export_storage_unavailable"

    def __init__(self) -> None:
        super().__init__("Export storage is not configured")


def status_of(row: models.DataExport) -> DataExportStatusResponse:
    return DataExportStatusResponse.model_validate(row)


async def start_export(
    db: AsyncSession, user: models.User
) -> tuple[models.DataExport, bool]:
    """Queue a new export; returns it and whether it is new.

    An export already pending or running is returned instead (False): a user
    has at most one in flight. Older ready exports expire and their files are
    deleted.
    """
    if not r2.is_configured():
        raise ExportStorageUnavailable()
    inflight = await crud.data_export.get_inflight(db, owner_id=user.id)
    if inflight:
        return inflight, False

    for row in await crud.data_export.expire_ready_for_owner(db, owner_id=user.id):
        if row.object_key:
            # A leftover file only costs storage; it expires anyway.
            with contextlib.suppress(Exception):
                await r2.delete_object(row.object_key)

    try:
        created = await crud.data_export.create_pending(
            db, owner_id=user.id, filename=export_filename()
        )
    except IntegrityError:
        # A concurrent request queued one first.
        await db.rollback()
        inflight = await crud.data_export.get_inflight(db, owner_id=user.id)
        if inflight:
            return inflight, False
        raise
    return created, True


async def latest_export(db: AsyncSession, user: models.User) -> models.DataExport:
    row = await crud.data_export.get_latest_for_owner(db, owner_id=user.id)
    if row is None:
        raise NotFoundError("No export found")
    return row


async def get_export(
    db: AsyncSession, user: models.User, export_id: UUID
) -> models.DataExport:
    row = await crud.data_export.get_for_owner(db, id=export_id, owner_id=user.id)
    if row is None:
        raise NotFoundError("Export not found")
    return row


async def download_link(row: models.DataExport) -> DataExportDownloadResponse:
    """A short-lived URL for a ready export's file."""
    if not status_of(row).download_available or not row.object_key:
        raise ConflictError("Export is not ready for download")
    if not r2.is_configured():
        raise ExportStorageUnavailable()
    url = await r2.presign_get(
        row.object_key, row.filename, settings.EXPORT_PRESIGN_EXPIRES_SECONDS
    )
    return DataExportDownloadResponse(
        url=url,
        expires_in=settings.EXPORT_PRESIGN_EXPIRES_SECONDS,
        filename=row.filename,
    )
