from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.api import deps
from app.schemas.data_export import DataExportDownloadResponse, DataExportStatusResponse
from app.services import data_exports as export_service
from app.utilities.wide_events import enrich_event

router = APIRouter()


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
    row, created = await export_service.start_export(db, current_user)
    if not created:
        enrich_event(request, export={"outcome": "already_inflight", "id": str(row.id)})
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=jsonable_encoder(export_service.status_of(row)),
        )
    enrich_event(request, export={"outcome": "created", "id": str(row.id)})
    return export_service.status_of(row)


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
    return export_service.status_of(
        await export_service.latest_export(db, current_user)
    )


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
    return export_service.status_of(
        await export_service.get_export(db, current_user, export_id)
    )


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
    row = await export_service.get_export(db, current_user, export_id)
    link = await export_service.download_link(row)
    enrich_event(request, export={"outcome": "presigned", "id": str(row.id)})
    return link
