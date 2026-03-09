from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
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

    if crud.user.is_superuser(current_user):
        places = await crud.place.get_multi(db, skip=skip, limit=limit)
    else:
        places = await crud.place.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
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

    place = await crud.place.create_with_owner(
        db=db, obj_in=place_in, owner_id=current_user.id
    )

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

    place = await crud.place.get(db=db, id=id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    if not crud.user.is_superuser(current_user) and (place.owner_id != current_user.id):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return place


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

    place = await read_place(db=db, id=id, current_user=current_user, request=request)

    place_in.updated_at = datetime.now(timezone.utc)
    place = await crud.place.update(db=db, db_obj=place, obj_in=place_in)
    return place


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

    place = await read_place(db=db, id=id, current_user=current_user, request=request)
    place = await crud.place.remove(db=db, id=id)
    return schemas.DeletionResponse(message=f"Place {id} deleted")
