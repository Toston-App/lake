from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.utilities.wide_events import enrich_event

router = APIRouter()


@router.get("/", response_model=list[schemas.Item])
async def read_items(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve items.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "list_items", "skip": skip, "limit": limit},
    )

    if crud.user.is_superuser(current_user):
        items = await crud.item.get_multi(db, skip=skip, limit=limit)
    else:
        items = await crud.item.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    enrich_event(request, database={"operation": "list_items", "results_count": len(items)})
    return items


@router.post("/", response_model=schemas.Item)
async def create_item(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    item_in: schemas.ItemCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new item.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "create_item"},
    )

    item = await crud.item.create_with_owner(
        db=db, obj_in=item_in, owner_id=current_user.id
    )
    return item


@router.get("/{id}", response_model=schemas.Item)
async def read_item(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get item by ID.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_item_by_id", "item_id": id},
    )

    item = await crud.item.get(db=db, id=id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not crud.user.is_superuser(current_user) and (item.owner_id != current_user.id):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return item


@router.put("/{id}", response_model=schemas.Item)
async def update_item(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    item_in: schemas.ItemUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an item.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "update_item", "item_id": id},
    )

    item = await read_item(request=request, db=db, id=id, current_user=current_user)
    item = await crud.item.update(db=db, db_obj=item, obj_in=item_in)
    return item


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_item(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an item.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "delete_item", "item_id": id},
    )

    item = await read_item(request=request, db=db, id=id, current_user=current_user)
    item = await crud.item.remove(db=db, id=id)
    return schemas.DeletionResponse(message=f"Item {id} deleted")
