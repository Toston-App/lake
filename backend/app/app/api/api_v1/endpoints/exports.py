from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.core.config import settings
from app.schemas.data_export import DataExportDownloadResponse, DataExportStatusResponse
from app.services import r2
from app.services.user_export import export_filename
from app.utilities.wide_events import enrich_event

router = APIRouter()


def _to_response(row: models.DataExport) -> DataExportStatusResponse:
    return DataExportStatusResponse.model_validate(row)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DataExportStatusResponse)
async def create_export(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "create_export"},
    )

    if not r2.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Export storage is not configured",
        )

    inflight = await crud.data_export.get_inflight(db, owner_id=current_user.id)
    if inflight:
        enrich_event(request, export={"outcome": "already_inflight", "id": str(inflight.id)})
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=jsonable_encoder(_to_response(inflight)),
        )

    previous = await crud.data_export.expire_ready_for_owner(
        db, owner_id=current_user.id
    )
    for row in previous:
        if row.object_key:
            try:
                await r2.delete_object(row.object_key)
            except Exception:
                pass

    try:
        created = await crud.data_export.create_pending(
            db, owner_id=current_user.id, filename=export_filename()
        )
    except IntegrityError:
        await db.rollback()
        inflight = await crud.data_export.get_inflight(db, owner_id=current_user.id)
        if inflight:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=jsonable_encoder(_to_response(inflight)),
            )
        raise

    enrich_event(request, export={"outcome": "created", "id": str(created.id)})
    return _to_response(created)


@router.get("/me", response_model=DataExportStatusResponse)
async def get_my_export(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "get_my_export"},
    )
    row = await crud.data_export.get_latest_for_owner(db, owner_id=current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="No export found")
    return _to_response(row)


@router.get("/{export_id}", response_model=DataExportStatusResponse)
async def get_export(
    request: Request,
    export_id: UUID,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "get_export"},
    )
    row = await crud.data_export.get_for_owner(
        db, id=export_id, owner_id=current_user.id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    return _to_response(row)


@router.get("/{export_id}/download", response_model=DataExportDownloadResponse)
async def download_export(
    request: Request,
    export_id: UUID,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "download_export"},
    )
    row = await crud.data_export.get_for_owner(
        db, id=export_id, owner_id=current_user.id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")

    payload = _to_response(row)
    if not payload.download_available or not row.object_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Export is not ready for download",
        )

    if not r2.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Export storage is not configured",
        )

    url = await r2.presign_get(
        row.object_key,
        row.filename,
        settings.EXPORT_PRESIGN_EXPIRES_SECONDS,
    )
    enrich_event(request, export={"outcome": "presigned", "id": str(row.id)})
    return DataExportDownloadResponse(
        url=url,
        expires_in=settings.EXPORT_PRESIGN_EXPIRES_SECONDS,
        filename=row.filename,
    )
