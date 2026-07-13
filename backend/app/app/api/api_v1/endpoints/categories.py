from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import crud, models, schemas
from app.api import deps
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

    if crud.user.is_superuser(current_user):
        categories = await crud.category.get_multi(db, skip=skip, limit=limit)
    else:
        categories = await crud.category.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
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

    category = await crud.category.create_with_owner(
        db=db, obj_in=category_in, owner_id=current_user.id
    )

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

    result = await db.execute(
        select(models.Category)
        .options(selectinload(models.Category.subcategories))
        .filter(models.Category.id == id)
    )
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    if not crud.user.is_superuser(current_user) and (
        category.owner_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    return category


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

    category = await read_category(db=db, id=id, current_user=current_user, request=request)

    # TODO: Check there are changes

    category_in.updated_at = datetime.now(timezone.utc)
    category = await crud.category.update(db=db, db_obj=category, obj_in=category_in)

    return category


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

    category = await read_category(db=db, id=id, current_user=current_user, request=request)

    if category.is_income:
        raise HTTPException(status_code=400, detail="This item cannot be deleted")

    await crud.category.remove(db=db, id=id)

    return schemas.DeletionResponse(message=f"Item {id} deleted")
