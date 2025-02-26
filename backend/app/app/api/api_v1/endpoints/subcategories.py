from datetime import datetime, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps

router = APIRouter()


@router.get("", response_model=List[schemas.Subcategory])
async def read_subcategories(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve subcategories.
    """
    if crud.user.is_superuser(current_user):
        categories = await crud.subcategory.get_multi(db, skip=skip, limit=limit)
    else:
        categories = await crud.subcategory.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    return categories


@router.post("", response_model=schemas.Subcategory)
async def create_subcategory(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    subcategory_in: schemas.SubcategoryCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new subcategory.
    """
    subcategory = await crud.subcategory.create_with_owner(
        db=db, obj_in=subcategory_in, owner_id=current_user.id
    )
    return subcategory


@router.get("/{id}", response_model=schemas.Subcategory)
async def read_subcategory(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get subcategory by ID.
    """
    subcategory = await crud.subcategory.get(db=db, id=id)
    if not subcategory:
        raise HTTPException(status_code=404, detail="Subcategory not found")
    if not crud.user.is_superuser(current_user) and (
        subcategory.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return subcategory


@router.put("/{id}", response_model=schemas.Subcategory)
async def update_subcategory(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    category_in: schemas.SubcategoryUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an subcategory.
    """
    subcategory = await read_subcategory(db=db, id=id, current_user=current_user)

    # TODO: Check there are changes
    if subcategory.is_default:
        return schemas.DeletionResponse(message=f"This item can not being deleted")

    category_in.updated_at = datetime.now(timezone.utc)
    subcategory = await crud.subcategory.update(
        db=db, db_obj=subcategory, obj_in=category_in
    )

    return subcategory


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_subcategory(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an subcategory.
    """
    subcategory = await read_subcategory(db=db, id=id, current_user=current_user)
    subcategory = await crud.subcategory.remove(db=db, id=id)

    return schemas.DeletionResponse(message=f"Item {id} deleted")
    return subcategory
