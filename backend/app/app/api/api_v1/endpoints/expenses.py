import calendar
from datetime import date as Date, timedelta, datetime, timezone
from enum import Enum
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps

router = APIRouter()


@router.get("/getAll", response_model=List[schemas.Expense])
async def read_expenses(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve expenses.
    """
    if crud.user.is_superuser(current_user):
        expenses = await crud.expense.get_multi(db, skip=skip, limit=limit)
    else:
        expenses = await crud.expense.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    return expenses


class DateFilterType(str, Enum):
    date = "date"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"
    range = "range"


@router.get("/{date_filter_type}/{date}", response_model=List[schemas.Expense])
async def read_expenses(
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: Date | str = None,
    to: Date | None = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve expenses filtered by type.
    """
    if date_filter_type == DateFilterType.date:
        if type(date) == str:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

        expenses = await crud.expense.get_multi_by_date(
            db=db, owner_id=current_user.id, start_date=date, end_date=date
        )

    if date_filter_type == DateFilterType.week:
        if type(date) == str:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

        end_date = date + timedelta(days=7)

        expenses = await crud.expense.get_multi_by_date(
            db=db, owner_id=current_user.id, start_date=date, end_date=end_date
        )

    if date_filter_type == DateFilterType.month:
        if isinstance(date, Date):
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM"
            )
        try:
            start_date = datetime.strptime(date, "%Y-%m").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM"
            )

        end_date = datetime.strptime(
            f"{start_date.year}-{start_date.month}-{calendar.monthrange(start_date.year, start_date.month)[1]}",
            "%Y-%m-%d",
        ).date()

        expenses = await crud.expense.get_multi_by_date(
            db=db, owner_id=current_user.id, start_date=start_date, end_date=end_date
        )

    if date_filter_type == DateFilterType.quarter:
        if isinstance(date, Date):
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format QX-YYYY"
            )

        try:
            year = date.split("-")[1]
            quarterNum = int(date.split("-")[0].replace("Q", ""))
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format QX-YYYY"
            )

        if quarterNum < 1 or quarterNum > 4:
            raise HTTPException(
                status_code=400, detail="Quarter must be between 1 and 4"
            )

        start_date = datetime.strptime(
            f"{year}-{(quarterNum - 1) * 3 + 1}-01", "%Y-%m-%d"
        ).date()
        end_date = datetime.strptime(
            f"{year}-{quarterNum * 3}-{calendar.monthrange(int(year), quarterNum * 3)[1]}",
            "%Y-%m-%d",
        ).date()

        expenses = await crud.expense.get_multi_by_date(
            db=db, owner_id=current_user.id, start_date=start_date, end_date=end_date
        )

    if date_filter_type == DateFilterType.year:
        if isinstance(date, Date) or not "x" in date or len(date.split("x")[0]) != 4:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYYx"
            )

        try:
            date = date.split("x")[0]
            start_date = datetime.strptime(f"{date}-01-01", "%Y-%m-%d").date()
            end_date = datetime.strptime(f"{date}-12-31", "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYYx"
            )

        expenses = await crud.expense.get_multi_by_date(
            db=db, owner_id=current_user.id, start_date=start_date, end_date=end_date
        )

    if date_filter_type == DateFilterType.range:
        if date_filter_type == DateFilterType.range and to is None:
            raise HTTPException(status_code=400, detail="Range requires two dates")

        if type(date) == str or type(to) == str:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

        if date > to:
            raise HTTPException(
                status_code=400, detail="Start date must be before end date"
            )

        expenses = await crud.expense.get_multi_by_date(
            db=db, owner_id=current_user.id, start_date=date, end_date=to
        )

    return expenses


@router.post("", response_model=schemas.Expense)
async def create_expense(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    expense_in: schemas.ExpenseCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new expense.
    """
    expense = await crud.expense.create_with_owner(
        db=db, obj_in=expense_in, owner_id=current_user.id
    )
    return expense


@router.post("/bulk", response_model=List[schemas.Expense])
async def create_expenses_bulk(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    expenses_in: List[schemas.ExpenseCreate],
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create multiple expenses at once.
    """
    expenses = await crud.expense.create_multi_with_owner(
        db=db, obj_list=expenses_in, owner_id=current_user.id
    )
    return expenses


@router.get("/{id}", response_model=schemas.Expense)
async def read_expense(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get expense by ID.
    """
    expense = await crud.expense.get(db=db, id=id)
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if not crud.user.is_superuser(current_user) and (
        expense.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return expense


@router.put("/{id}", response_model=schemas.Expense)
async def update_expense(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    expense_in: schemas.ExpenseUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an expense.
    """
    expense = await read_expense(db=db, id=id, current_user=current_user)

    # TODO: Check there are changes
    # Store original values for later comparison
    original_amount = expense.amount
    original_account_id = expense.account_id

    if expense_in.place_id:
        place = await crud.place.get(db=db, id=expense_in.place_id)
        if not place:
            expense_in.place_id = expense.place_id

    if expense_in.category_id:
        category = await crud.category.get(db=db, id=expense_in.category_id)
        if not category:
            expense_in.category_id = expense.category_id

    if expense_in.subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=expense_in.subcategory_id)
        if not subcategory:
            expense_in.subcategory_id = expense.subcategory_id

    if expense_in.date:
        try:
            expense_in.date = datetime.strptime(expense_in.date, "%Y-%m-%d").date()
        except:
            expense_in.date = expense.date

    if expense_in.account_id:
        account = await crud.account.get(db=db, id=expense_in.account_id)
        if not account:
            expense_in.account_id = expense.account_id

    expense_in.updated_at = datetime.now(timezone.utc)

    # Update the expense in the database
    updated_expense = await crud.expense.update(
        db=db, db_obj=expense, obj_in=expense_in
    )

    if (
        updated_expense.amount != original_amount
        or updated_expense.account_id != original_account_id
    ):
        # Update original account
        if original_account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                id=original_account_id,
                column="total_expenses",
                amount=-original_amount,
            )

        # Update new account
        if updated_expense.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                id=updated_expense.account_id,
                column="total_expenses",
                amount=updated_expense.amount,
            )

        # Update user's global balance
        if updated_expense.amount != original_amount:
            amount_difference = updated_expense.amount - original_amount
            await crud.user.update_balance(
                db=db,
                user_id=current_user.id,
                is_Expense=True,
                amount=amount_difference,
            )

    return updated_expense


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_expense(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an expense.
    """
    expense = await read_expense(db=db, id=id, current_user=current_user)
    expense = await crud.expense.remove(db=db, id=id)

    # Remove the expense from the user's balance
    await crud.user.update_balance(
        db=db, user_id=current_user.id, is_Expense=True, amount=-expense.amount
    )

    if expense.account_id:
        # amount is negative because it's an expense, and we want to subtract instead of add
        await crud.account.update_by_id_and_field(
            db=db,
            id=expense.account_id,
            column="total_expenses",
            amount=-expense.amount,
        )

    return schemas.DeletionResponse(message=f"Item {id} deleted")
    return expense


@router.delete("/bulk/{ids}", response_model=schemas.BulkDeletionResponse)
async def delete_expenses_bulk(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    ids: str,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete multiple expenses at once.
    Format: /bulk/1,2,3
    """
    try:
        id_list = [int(id.strip()) for id in ids.split(",")]
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid ID format. Use comma-separated integers"
        )

    # Verify permissions for all expenses
    valid_ids = []
    for id in id_list:
        expense = await crud.expense.get(db=db, id=id)
        if not expense:
            continue
        if not crud.user.is_superuser(current_user) and (
            expense.owner_id != current_user.id
        ):
            raise HTTPException(
                status_code=400, detail=f"Not enough permissions for expense {id}"
            )
        valid_ids.append(expense.id)

    if not valid_ids:
        raise HTTPException(status_code=404, detail="No valid expenses found")

    removed_expenses = await crud.expense.remove_multi(db=db, ids=valid_ids)

    # Update balances
    for expense in removed_expenses:
        await crud.user.update_balance(
            db=db, user_id=current_user.id, is_Expense=True, amount=-expense.amount
        )
        if expense.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                id=expense.account_id,
                column="total_expenses",
                amount=-expense.amount,
            )

    return schemas.BulkDeletionResponse(
        message=f"Deleted {len(removed_expenses)} expenses",
        deleted_ids=[e.id for e in removed_expenses],
    )
