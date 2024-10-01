import calendar
from datetime import date as Date, timedelta, datetime, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.api.deps import DateFilterType

router = APIRouter()


@router.get("/getAll", response_model=List[schemas.Transfer])
async def read_transfers(
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

@router.get("/{date_filter_type}/{date}", response_model=List[schemas.Transfer])
async def read_transfers(
        db: AsyncSession = Depends(deps.async_get_db),
        date_filter_type: DateFilterType = DateFilterType.date,
        date: Date | str = None,
        to: Date | None = None,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve transfers filtered by type.
    """
    if date_filter_type == DateFilterType.date:
        if type(date) == str:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM-DD")

        transfers = await crud.transfer.get_multi_by_date(db=db, start_date=date, end_date=date)

    if date_filter_type == DateFilterType.week:
        if type(date) == str:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM-DD")

        end_date = date + timedelta(days=7)

        transfers = await crud.transfer.get_multi_by_date(db=db, start_date=date, end_date=end_date)

    if date_filter_type == DateFilterType.month:
        if isinstance(date, Date):
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM")
        try:
            start_date = datetime.strptime(date, "%Y-%m").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM")

        end_date =  datetime.strptime(f"{start_date.year}-{start_date.month}-{calendar.monthrange(start_date.year, start_date.month)[1]}", "%Y-%m-%d").date()

        transfers = await crud.transfer.get_multi_by_date(db=db, start_date=start_date, end_date=end_date)

    if date_filter_type == DateFilterType.quarter:
        if isinstance(date, Date):
            raise HTTPException(status_code=400, detail="Date must be a date in the format QX-YYYY")

        try:
            year = date.split("-")[1]
            quarterNum = int(date.split("-")[0].replace("Q", ""))
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be a date in the format QX-YYYY")

        if quarterNum < 1 or quarterNum > 4:
            raise HTTPException(status_code=400, detail="Quarter must be between 1 and 4")


        start_date = datetime.strptime(f"{year}-{(quarterNum - 1) * 3 + 1}-01", "%Y-%m-%d").date()
        end_date =  datetime.strptime(f"{year}-{quarterNum * 3}-{calendar.monthrange(int(year), quarterNum * 3)[1]}", "%Y-%m-%d").date()

        transfers = await crud.transfer.get_multi_by_date(db=db, start_date=start_date, end_date=end_date)

    if date_filter_type == DateFilterType.year:
        if isinstance(date, Date) or not "x" in date or len(date.split("x")[0]) != 4 :
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYYx")

        try:
            date = date.split("x")[0]
            start_date = datetime.strptime(f"{date}-01-01", "%Y-%m-%d").date()
            end_date =  datetime.strptime(f"{date}-12-31", "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYYx")

        transfers = await crud.transfer.get_multi_by_date(db=db, start_date=start_date, end_date=end_date)

    if date_filter_type == DateFilterType.range:
        if(date_filter_type == DateFilterType.range and to is None):
            raise HTTPException(status_code=400, detail="Range requires two dates")

        if type(date) == str or type(to) == str:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM-DD")

        if date > to:
            raise HTTPException(status_code=400, detail="Start date must be before end date")

        transfers = await crud.transfer.get_multi_by_date(db=db, start_date=date, end_date=to)

    return transfers

@router.post("/", response_model=schemas.Transfer)
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
    if not crud.user.is_superuser(current_user) and (transfer.owner_id != current_user.id):
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
    Update an transfer.
    """
    transfer = await read_transfer(db=db, id=id, current_user=current_user)
    # TODO: Check there are changes

    if transfer_in.from_acc:
        # Remove from old account
        amount = transfer_in.amount or transfer.amount
        await crud.account.update_by_id_and_field(db=db, id=transfer.from_acc, column='total_transfers_out', amount=-amount)

        # Add to new account
        await crud.account.update_by_id_and_field(db=db, id=transfer_in.from_acc, column='total_transfers_out', amount=amount)

    if transfer_in.to_acc:
        # Remove from old account
        amount = transfer_in.amount or transfer.amount
        await crud.account.update_by_id_and_field(db=db, id=transfer.to_acc, column='total_transfers_in', amount=-amount)

        # Add to new account
        await crud.account.update_by_id_and_field(db=db, id=transfer_in.to_acc, column='total_transfers_in', amount=amount)


    transfer_in.updated_at = datetime.now(timezone.utc)
    transfer = await crud.transfer.update(db=db, db_obj=transfer, obj_in=transfer_in)

    return transfer

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
    await crud.account.update_by_id_and_field(db=db, id=transfer.from_acc, column='total_transfers_out', amount=-transfer.amount)
    await crud.account.update_by_id_and_field(db=db, id=transfer.to_acc, column='total_transfers_in', amount=-transfer.amount)

    return schemas.DeletionResponse(message=f"Item {id} deleted")
    return transfer
