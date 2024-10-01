import calendar

from datetime import date as Date, timedelta, datetime, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.api.deps import DateFilterType

router = APIRouter()


@router.get("/getAll", response_model=List[schemas.Income])
async def read_incomes(
        db: AsyncSession = Depends(deps.async_get_db),
        skip: int = 0,
        limit: int = 100,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve incomes.
    """
    if crud.user.is_superuser(current_user):
        incomes = await crud.income.get_multi(db, skip=skip, limit=limit)
    else:
        incomes = await crud.income.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    return incomes

@router.get("/{date_filter_type}/{date}", response_model=List[schemas.Income])
async def read_incomes(
        db: AsyncSession = Depends(deps.async_get_db),
        date_filter_type: DateFilterType = DateFilterType.date,
        date: Date | str = None,
        to: Date | None = None,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve incomes filtered by type.
    """
    if date_filter_type == DateFilterType.date:
        if type(date) == str:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM-DD")

        incomes = await crud.income.get_multi_by_date(db=db, start_date=date, end_date=date)

    if date_filter_type == DateFilterType.week:
        if type(date) == str:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM-DD")

        end_date = date + timedelta(days=7)

        incomes = await crud.income.get_multi_by_date(db=db, start_date=date, end_date=end_date)

    if date_filter_type == DateFilterType.month:
        if isinstance(date, Date):
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM")
        try:
            start_date = datetime.strptime(date, "%Y-%m").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM")

        end_date =  datetime.strptime(f"{start_date.year}-{start_date.month}-{calendar.monthrange(start_date.year, start_date.month)[1]}", "%Y-%m-%d").date()

        incomes = await crud.income.get_multi_by_date(db=db, start_date=start_date, end_date=end_date)

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

        incomes = await crud.income.get_multi_by_date(db=db, start_date=start_date, end_date=end_date)

    if date_filter_type == DateFilterType.year:
        if isinstance(date, Date) or not "x" in date or len(date.split("x")[0]) != 4 :
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYYx")

        try:
            date = date.split("x")[0]
            start_date = datetime.strptime(f"{date}-01-01", "%Y-%m-%d").date()
            end_date =  datetime.strptime(f"{date}-12-31", "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYYx")

        incomes = await crud.income.get_multi_by_date(db=db, start_date=start_date, end_date=end_date)

    if date_filter_type == DateFilterType.range:
        if(date_filter_type == DateFilterType.range and to is None):
            raise HTTPException(status_code=400, detail="Range requires two dates")

        if type(date) == str or type(to) == str:
            raise HTTPException(status_code=400, detail="Date must be a date in the format YYYY-MM-DD")

        if date > to:
            raise HTTPException(status_code=400, detail="Start date must be before end date")

        incomes = await crud.income.get_multi_by_date(db=db, start_date=date, end_date=to)

    return incomes

@router.post("", response_model=schemas.Income)
async def create_income(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        income_in: schemas.IncomeCreate,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new income.
    """
    income = await crud.income.create_with_owner(
        db=db, obj_in=income_in, owner_id=current_user.id
    )
    return income


@router.get("/{id}", response_model=schemas.Income)
async def read_income(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        id: int,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get income by ID.
    """
    income = await crud.income.get(db=db, id=id)
    if not income:
        raise HTTPException(status_code=404, detail="Income not found")
    if not crud.user.is_superuser(current_user) and (income.owner_id != current_user.id):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return income


@router.put("/{id}", response_model=schemas.Income)
async def update_income(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        id: int,
        income_in: schemas.IncomeUpdate,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an income.
    """
    income = await read_income(db=db, id=id, current_user=current_user)

    # TODO: Check there are changes
    # Store original values for later comparison
    original_amount = income.amount
    original_account_id = income.account_id

    if income_in.place_id:
        place = await crud.place.get(db=db, id=income_in.place_id)
        if not place:
            income_in.place_id = income.place_id

    if income_in.subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=income_in.subcategory_id)
        if not subcategory:
            income_in.subcategory_id = income.subcategory_id

    if income_in.date:
        try:
            income_in.date = datetime.strptime(income_in.date, "%Y-%m-%d").date()
        except:
            income.date = income.date

    if income_in.account_id:
        account = await crud.account.get(db=db, id=income_in.account_id)
        if not account:
            income_in.account_id = income.account_id


    income_in.updated_at = datetime.now(timezone.utc)
    updated_income = await crud.income.update(db=db, db_obj=income, obj_in=income_in)

    if updated_income.amount != original_amount or updated_income.account_id != original_account_id:
        # Update original account
        if original_account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                id=original_account_id,
                column='total_incomes',
                amount=-original_amount
            )

        # Update new account
        if updated_income.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                id=updated_income.account_id,
                column='total_incomes',
                amount=updated_income.amount
            )

        # Update user's global balance
        if updated_income.amount != original_amount:
            amount_difference = updated_income.amount - original_amount
            await crud.user.update_balance(
                db=db,
                user_id=current_user.id,
                is_Expense=False,
                amount=amount_difference
            )

    return updated_income


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_income(
        *,
        db: AsyncSession = Depends(deps.async_get_db),
        id: int,
        current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an income.
    """
    income = await read_income(db=db, id=id, current_user=current_user)
    income = await crud.income.remove(db=db, id=id)

    # Remove the income from the user's balance
    await crud.user.update_balance(db=db, user_id=current_user.id, is_Expense=False, amount=-income.amount)

    if income.account_id:
        # amount is negative because it's an income, and we want to subtract instead of add
        await crud.account.update_by_id_and_field(db=db, id=income.account_id, column='total_incomes', amount=-income.amount)


    return schemas.DeletionResponse(message=f"Item {id} deleted")
    return income
