from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps

router = APIRouter()


@router.get("", response_model=list[schemas.Category])
async def read_categories(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve categories.
    """
    if crud.user.is_superuser(current_user):
        categories = await crud.category.get_multi(db, skip=skip, limit=limit)
    else:
        categories = await crud.category.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    return categories


@router.post("", response_model=schemas.Category)
async def create_category(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    category_in: schemas.CategoryCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new category.
    """
    category = await crud.category.create_with_owner(
        db=db, obj_in=category_in, owner_id=current_user.id
    )
    return category


@router.get("/{id}", response_model=schemas.Category)
async def read_category(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get category by ID.
    """
    category = await crud.category.get(db=db, id=id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    if not crud.user.is_superuser(current_user) and (
        category.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return category


@router.put("/{id}", response_model=schemas.Category)
async def update_category(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    category_in: schemas.CategoryUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an category.
    """
    category = await read_category(db=db, id=id, current_user=current_user)

    # TODO: Check there are changes

    category_in.updated_at = datetime.now(timezone.utc)
    category = await crud.category.update(db=db, db_obj=category, obj_in=category_in)

    return category


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_category(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an category.
    """
    category = await read_category(db=db, id=id, current_user=current_user)

    if category.is_income:
        raise HTTPException(status_code=400, detail="This item cannot be deleted")

    category = await crud.category.remove(db=db, id=id)

    return schemas.DeletionResponse(message=f"Item {id} deleted")
