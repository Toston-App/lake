from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.services import catalog as catalog_service
from app.services.errors import ServiceError, raise_http
from app.utilities.wide_events import enrich_event

router = APIRouter()


@router.get("", response_model=list[schemas.Subcategory])
async def read_subcategories(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve subcategories.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "list_subcategories", "skip": skip, "limit": limit},
    )

    categories = await catalog_service.list_subcategories(
        db, current_user, skip=skip, limit=limit
    )

    enrich_event(request, database={"operation": "list_subcategories", "results_count": len(categories)})
    return categories


@router.post("", response_model=schemas.Subcategory)
async def create_subcategory(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    subcategory_in: schemas.SubcategoryCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new subcategory.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "create_subcategory"},
    )

    try:
        subcategory = await catalog_service.create_subcategory(
            db, current_user, subcategory_in
        )
    except ServiceError as exc:
        raise_http(exc)

    enrich_event(request, database={"operation": "create_subcategory", "id": subcategory.id})
    return subcategory


@router.get("/{id}", response_model=schemas.Subcategory)
async def read_subcategory(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get subcategory by ID.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_subcategory_by_id", "subcategory_id": id},
    )

    try:
        return await catalog_service.get_subcategory(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)


@router.put("/{id}", response_model=schemas.Subcategory)
async def update_subcategory(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    category_in: schemas.SubcategoryUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an subcategory.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "update_subcategory", "subcategory_id": id},
    )

    try:
        return await catalog_service.update_subcategory(
            db, current_user, id, category_in
        )
    except ServiceError as exc:
        raise_http(exc)


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_subcategory(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an subcategory.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "delete_subcategory", "subcategory_id": id},
    )

    try:
        await catalog_service.delete_subcategory(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)

    return schemas.DeletionResponse(message=f"Item {id} deleted")
