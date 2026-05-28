from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.services import catalog as catalog_service
from app.services.errors import ServiceError, raise_http
from app.utilities.wide_events import enrich_event

router = APIRouter()


@router.get("", response_model=list[schemas.Category])
async def read_categories(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve categories.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "list_categories", "skip": skip, "limit": limit},
    )

    categories = await catalog_service.list_categories(
        db, current_user, skip=skip, limit=limit
    )

    enrich_event(request, database={"operation": "list_categories", "results_count": len(categories)})
    return categories


@router.post("", response_model=schemas.Category)
async def create_category(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    category_in: schemas.CategoryCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new category.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "create_category"},
    )

    category = await catalog_service.create_category(db, current_user, category_in)

    enrich_event(request, database={"operation": "create_category", "id": category.id})
    return category


@router.get("/{id}", response_model=schemas.Category)
async def read_category(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get category by ID.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_category_by_id", "category_id": id},
    )

    try:
        return await catalog_service.get_category(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)


@router.put("/{id}", response_model=schemas.Category)
async def update_category(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    category_in: schemas.CategoryUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an category.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "update_category", "category_id": id},
    )

    try:
        return await catalog_service.update_category(db, current_user, id, category_in)
    except ServiceError as exc:
        raise_http(exc)


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_category(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete a category.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "delete_category", "category_id": id},
    )

    try:
        await catalog_service.delete_category(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)

    return schemas.DeletionResponse(message=f"Item {id} deleted")
