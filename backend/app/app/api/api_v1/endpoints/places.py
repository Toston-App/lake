from datetime import datetime, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps

router = APIRouter()


@router.get("", response_model=List[schemas.Place])
async def read_places(
        db: AsyncSession = Depends(deps.async_get_db),
        skip: int = 0,
        limit: int = 100,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve places.
    """
    if crud.user.is_superuser(current_user):
        places = await crud.place.get_multi(db, skip=skip, limit=limit)
    else:
        places = await crud.place.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )
    return places


@router.post("", response_model=schemas.Place)
async def create_place(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        place_in: schemas.PlaceCreate,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new place.
    """
    place = await crud.place.create_with_owner(db=db, obj_in=place_in, owner_id=current_user.id)
    return place


@router.get("/{id}", response_model=schemas.Place)
async def read_place(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        id: int,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get place by ID.
    """
    place = await crud.place.get(db=db, id=id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    if not crud.user.is_superuser(current_user) and (place.owner_id != current_user.id):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return place


@router.put("/{id}", response_model=schemas.Place)
async def update_place(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        id: int,
        place_in: schemas.PlaceUpdate,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an place.
    """
    place = await read_place(db=db, id=id, current_user=current_user)

    place_in.updated_at = datetime.now(timezone.utc)
    place = await crud.place.update(db=db, db_obj=place, obj_in=place_in)
    return place


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_place(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        id: int,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an place.
    """
    place = await read_place(db=db, id=id, current_user=current_user)
    place = await crud.place.remove(db=db, id=id)
    return schemas.DeletionResponse(message=f"Place {id} deleted")
