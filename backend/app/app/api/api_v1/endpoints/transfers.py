from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.api.deps import DateFilterType

router = APIRouter()


@router.get("/getAll", response_model=list[schemas.Transfer])
async def read_all_transfers(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve transfers.
    """
    if crud.user.is_superuser(current_user):
        transfers = await crud.transfer.get_multi(db, skip=skip, limit=limit)
    else:
        transfers = await crud.transfer.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    return transfers


@router.get("/{date_filter_type}/{date}", response_model=list[schemas.Transfer])
async def read_transfers(
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: str = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve transfers filtered by type.
    """
    start_date, end_date = deps.parse_date_filter(date_filter_type, date)

    if start_date and end_date:
        transfers = await crud.transfer.get_multi_by_date(
            db=db, owner_id=current_user.id, start_date=start_date, end_date=end_date
        )
        return transfers

    return []


@router.post("", response_model=schemas.Transfer)
async def create_transfer(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    transfer_in: schemas.TransferCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new transfer.
    """
    transfer = await crud.transfer.create_with_owner(
        db=db, obj_in=transfer_in, owner_id=current_user.id
    )

    if transfer is None:
        raise HTTPException(status_code=400, detail="Account not found")

    return transfer


@router.get("/{id}", response_model=schemas.Transfer)
async def read_transfer(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get transfer by ID.
    """
    transfer = await crud.transfer.get(db=db, id=id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if not crud.user.is_superuser(current_user) and (
        transfer.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return transfer


@router.put("/{id}", response_model=schemas.Transfer)
async def update_transfer(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    transfer_in: schemas.TransferUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update a transfer.
    """
    existing_transfer = await read_transfer(db=db, id=id, current_user=current_user)

    # Update the transfer using owner-aware method
    try:
        transfer_in.updated_at = datetime.now(timezone.utc)
        updated_transfer = await crud.transfer.update_with_owner(
            db=db, db_obj=existing_transfer, obj_in=transfer_in, owner_id=current_user.id
        )
        if not updated_transfer:
            raise HTTPException(status_code=400, detail="Error updating transfer.")
    except Exception:
        raise HTTPException(status_code=400, detail="Error updating transfer.")

    return updated_transfer

@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_transfer(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an transfer.
    """
    transfer = await crud.transfer.remove_with_owner(db=db, transfer_id=id, owner_id=current_user.id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    return schemas.DeletionResponse(message=f"Item {id} deleted")
