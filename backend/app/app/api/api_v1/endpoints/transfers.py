import calendar
from datetime import date as Date
from datetime import datetime, timedelta, timezone
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
    start_date: Date | None = None
    end_date: Date | None = None

    if date_filter_type == DateFilterType.date:
        try:
            start_date = datetime.strptime(date, "%Y-%m-%d").date()
            end_date = start_date
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

    elif date_filter_type == DateFilterType.week:
        try:
            start_date = datetime.strptime(date, "%Y-%m-%d").date()
            end_date = start_date + timedelta(days=7)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

    elif date_filter_type == DateFilterType.month:
        try:
            start_date = datetime.strptime(date, "%Y-%m").date()
            _, num_days = calendar.monthrange(start_date.year, start_date.month)
            end_date = start_date + timedelta(days=num_days - 1)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY-MM"
            )

    elif date_filter_type == DateFilterType.quarter:
        try:
            year_str, quarter_str = date.split("-")
            quarterNum = int(quarter_str.replace("Q", ""))
            year = int(year_str)

            if quarterNum < 1 or quarterNum > 4:
                raise ValueError("Quarter must be between 1 and 4")

            start_month = (quarterNum - 1) * 3 + 1
            end_month = quarterNum * 3
            start_date = Date(year, start_month, 1)
            _, end_day = calendar.monthrange(year, end_month)
            end_date = Date(year, end_month, end_day)

        except (ValueError, IndexError):
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY-QX"
            )

    elif date_filter_type == DateFilterType.year:
        try:
            year = int(date)
            start_date = Date(year, 1, 1)
            end_date = Date(year, 12, 31)
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY"
            )

    elif date_filter_type == DateFilterType.range:
        try:
            start_date_str, end_date_str = date.split(":")
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Date range must be in the format YYYY-MM-DD:YYYY-MM-DD",
            )

        if start_date > end_date:
            raise HTTPException(
                status_code=400, detail="Start date must be before end date"
            )

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

    # Store original values for comparison
    original_from_acc = existing_transfer.from_acc
    original_to_acc = existing_transfer.to_acc
    original_amount = existing_transfer.amount

    # Update the transfer
    try:
        transfer_in.updated_at = datetime.now(timezone.utc)
        updated_transfer = await crud.transfer.update(
            db=db, db_obj=existing_transfer, obj_in=transfer_in
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Error updating transfer.")

    # Handle source account change
    if transfer_in.from_acc is not None and original_from_acc != transfer_in.from_acc:
        # Remove amount from old source account
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=current_user.id,
            id=original_from_acc,
            column="total_transfers_out",
            amount=-original_amount,
        )
        # Add amount to new source account
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=current_user.id,
            id=transfer_in.from_acc,
            column="total_transfers_out",
            amount=updated_transfer.amount,
        )

    # Handle destination account change
    if transfer_in.to_acc is not None and original_to_acc != transfer_in.to_acc:
        # Remove amount from old destination account
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=current_user.id,
            id=original_to_acc,
            column="total_transfers_in",
            amount=-original_amount,
        )
        # Add amount to new destination account
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=current_user.id,
            id=transfer_in.to_acc,
            column="total_transfers_in",
            amount=updated_transfer.amount,
        )

    # Handle amount change (when accounts remain the same)
    if (transfer_in.amount is not None and
        original_amount != transfer_in.amount and
        (transfer_in.from_acc is None or original_from_acc == transfer_in.from_acc) and
        (transfer_in.to_acc is None or original_to_acc == transfer_in.to_acc)):

        amount_difference = transfer_in.amount - original_amount

        # Adjust source account balance
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=current_user.id,
            id=updated_transfer.from_acc,
            column="total_transfers_out",
            amount=amount_difference,
        )

        # Adjust destination account balance
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=current_user.id,
            id=updated_transfer.to_acc,
            column="total_transfers_in",
            amount=amount_difference,
        )

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
    transfer = await read_transfer(db=db, id=id, current_user=current_user)
    transfer = await crud.transfer.remove(db=db, id=id)

    # TODO: Do it in a single query or concurrently with asyncio
    await crud.account.update_by_id_and_field(
        db=db,
        owner_id=current_user.id,
        id=transfer.from_acc,
        column="total_transfers_out",
        amount=-transfer.amount,
    )
    await crud.account.update_by_id_and_field(
        db=db,
        owner_id=current_user.id,
        id=transfer.to_acc,
        column="total_transfers_in",
        amount=-transfer.amount
    )

    return schemas.DeletionResponse(message=f"Item {id} deleted")
