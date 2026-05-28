from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.services import catalog as catalog_service
from app.services.errors import ServiceError, raise_http
from app.utilities.wide_events import enrich_event

router = APIRouter()


@router.get("", response_model=list[schemas.Place])
async def read_places(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve places.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "list_places", "skip": skip, "limit": limit},
    )

    places = await catalog_service.list_places(
        db, current_user, skip=skip, limit=limit
    )

    enrich_event(request, database={"operation": "list_places", "results_count": len(places)})
    return places


@router.post("", response_model=schemas.Place)
async def create_place(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    place_in: schemas.PlaceCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new place.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "create_place"},
    )

    place = await catalog_service.create_place(db, current_user, place_in)

    enrich_event(request, database={"operation": "create_place", "id": place.id})
    return place


@router.get("/{id}", response_model=schemas.Place)
async def read_place(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get place by ID.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_place_by_id", "place_id": id},
    )

    try:
        return await catalog_service.get_place(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)


@router.put("/{id}", response_model=schemas.Place)
async def update_place(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    place_in: schemas.PlaceUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an place.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "update_place", "place_id": id},
    )

    try:
        return await catalog_service.update_place(db, current_user, id, place_in)
    except ServiceError as exc:
        raise_http(exc)


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_place(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an place.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "delete_place", "place_id": id},
    )

    try:
        await catalog_service.delete_place(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)
    return schemas.DeletionResponse(message=f"Place {id} deleted")
