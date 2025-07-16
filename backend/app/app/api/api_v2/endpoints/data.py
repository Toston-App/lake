import asyncio
import calendar
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.api.deps import DateFilterType
from app.process_data.process import (
    account_charts,
    account_diff,
    accounts_total,
    categories_charts,
    get_df,
    transaction_charts,
)

router = APIRouter()


@router.get("/getAll", response_model=list[schemas.Data])
async def read_all_expenses(
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


async def all_querys(
    db, start_date, end_date, type="days", time_difference=0, owner_id=None
):
    reldelta = (
        relativedelta(days=time_difference)
        if type == "days"
        else relativedelta(months=time_difference)
    )

    incomes_actual_task = crud.income.get_multi_by_date(
        db=db, owner_id=owner_id, start_date=start_date, end_date=end_date
    )
    incomes_past_task = crud.income.get_multi_by_date(
        db=db,
        owner_id=owner_id,
        start_date=start_date - reldelta,
        end_date=end_date - reldelta,
    )
    expenses_actual_task = crud.expense.get_multi_by_date(
        db=db, owner_id=owner_id, start_date=start_date, end_date=end_date
    )
    expenses_past_task = crud.expense.get_multi_by_date(
        db=db,
        owner_id=owner_id,
        start_date=start_date - reldelta,
        end_date=end_date - reldelta,
    )
    transfers_task = crud.transfer.get_multi_by_date(db=db, owner_id=owner_id, start_date=start_date, end_date=end_date)

    accounts_task = crud.account.get_multi_by_owner(db=db, owner_id=owner_id)
    places_task = crud.place.get_multi_by_owner(db=db, owner_id=owner_id)
    categories_task = crud.category.get_multi_by_owner(db=db, owner_id=owner_id)
    # subcategories_task = crud.subcategory.get_multi_by_owner(db=db, owner_id=owner_id)

    results = await asyncio.gather(
        incomes_actual_task,
        incomes_past_task,
        expenses_actual_task,
        expenses_past_task,
        transfers_task,
        accounts_task,
        places_task,
        categories_task,
        # subcategories_task
    )

    return results


@router.get("/{date_filter_type}/{date}", response_model=Any)
async def get_all_data(
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: Date | str = None,
    to: Date | None = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Massive data retrieval for the dashboard.
    """
    if date_filter_type == DateFilterType.date:
        if type(date) == str:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

        results = await all_querys(db, date, date, owner_id=current_user.id)

    if date_filter_type == DateFilterType.week:
        if type(date) == str:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

        end_date = date + timedelta(days=6)

        results = await all_querys(
            db, date, end_date, "days", 6, owner_id=current_user.id
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

        results = await all_querys(
            db, start_date, end_date, "months", 1, owner_id=current_user.id
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

        results = await all_querys(
            db, start_date, end_date, "months", 3, owner_id=current_user.id
        )

    if date_filter_type == DateFilterType.year:
        if isinstance(date, Date) or "x" not in date or len(date.split("x")[0]) != 4:
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

        results = await all_querys(
            db, start_date, end_date, "days", 365, owner_id=current_user.id
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

        results = await all_querys(
            db, date, to, "days", (to - date).days, owner_id=current_user.id
        )

    (
        incomes_actual,
        incomes_past,
        expenses_actual,
        expenses_past,
        transfers,
        accounts,
        places,
        categories,
    ) = results

    if incomes_actual == [] and expenses_actual == []:
        return {
            "currency": current_user.country,
            "language": current_user.country,
            "accounts": jsonable_encoder(accounts),
            "balance": {
                "total": round(current_user.balance_total, 2),
                "income": round(current_user.balance_income, 2),
                "outcome": round(current_user.balance_outcome, 2),
            },
            "incomes": [],
            "expenses": [],
            "transfers": jsonable_encoder(transfers),
            "charts": {
                "transactions": [],
                "categories": [],
                "accounts_growth": [],
                "accounts": [],
            },
        }

    dfs = get_df(
        expenses=jsonable_encoder(expenses_actual),
        incomes=jsonable_encoder(incomes_actual),
        transfers=jsonable_encoder(transfers),
        accounts=jsonable_encoder(accounts),
        places=jsonable_encoder(places),
        categories=jsonable_encoder(categories),
    )
    # print("🚀 ~ file: data.py:158 ~ dfs:", dfs)
    past_dfs = get_df(
        expenses=jsonable_encoder(expenses_past),
        incomes=jsonable_encoder(incomes_past),
        transfers=jsonable_encoder(transfers),
        accounts=jsonable_encoder(accounts),
        places=jsonable_encoder(places),
        categories=jsonable_encoder(categories),
    )

    transaction_chart = transaction_charts(
        date_filter_type=date_filter_type,
        expenses_df=dfs["expenses"],
        incomes_df=dfs["incomes"],
    )
    categories_chart = categories_charts(
        expenses_df=dfs["expenses"], incomes_df=dfs["incomes"]
    )
    past_accounts_total = accounts_total(
        incomes_df=past_dfs["incomes"], expenses_df=past_dfs["expenses"]
    )
    actual_accounts_total = accounts_total(
        incomes_df=dfs["incomes"], expenses_df=dfs["expenses"]
    )
    accounts_growth = account_diff(
        past=past_accounts_total, actual=actual_accounts_total
    )
    account_chart = account_charts(
        incomes_df=dfs["incomes"], expenses_df=dfs["expenses"], transfers_df=dfs["transfers"]
    )

    return {
        "currency": current_user.country,
        "language": current_user.country,
        "accounts": jsonable_encoder(accounts),
        "balance": {
            "total": round(current_user.balance_total, 2),
            "income": round(current_user.balance_income, 2),
            "outcome": round(current_user.balance_outcome, 2),
        },
        "incomes": jsonable_encoder(incomes_actual),
        "expenses": jsonable_encoder(expenses_actual),
        "transfers": jsonable_encoder(transfers),
        "charts": {
            "transactions": transaction_chart,
            "categories": categories_chart,
            "accounts_growth": accounts_growth,
            "accounts": account_chart,
        },
    }


# @router.post("/", response_model=schemas.Expense)
# async def create_expense(
#         *,
#         db: AsyncSession = Depends(deps.async_get_db),
#         expense_in: schemas.ExpenseCreate,
#         current_user: models.User = Depends(deps.get_current_active_user),
# ) -> Any:
#     """
#     Create new expense.
#     """
#     expense = await crud.expense.create_with_owner(
#         db=db, obj_in=expense_in, owner_id=current_user.id
#     )
#     return expense


# @router.get("/{id}", response_model=schemas.Expense)
# async def read_expense(
#         *,
#         db: AsyncSession = Depends(deps.async_get_db),
#         id: int,
#         current_user: models.User = Depends(deps.get_current_active_user),
# ) -> Any:
#     """
#     Get expense by ID.
#     """
#     expense = await crud.expense.get(db=db, id=id)
#     if not expense:
#         raise HTTPException(status_code=404, detail="Expense not found")
#     if not crud.user.is_superuser(current_user) and (expense.owner_id != current_user.id):
#         raise HTTPException(status_code=400, detail="Not enough permissions")
#     return expense


# @router.put("/{id}", response_model=schemas.Expense)
# async def update_expense(
#         *,
#         db: AsyncSession = Depends(deps.async_get_db),
#         id: int,
#         expense_in: schemas.ExpenseUpdate,
#         current_user: models.User = Depends(deps.get_current_active_user),
# ) -> Any:
#     """
#     Update an expense.
#     """
#     expense = await read_expense(db=db, id=id, current_user=current_user)

#     # TODO: Check there are changes

#     # Update the account total expenses
#     if expense_in.account_id and expense.account_id:
#         amount = expense_in.amount or expense.amount

#         # amount is negative because it's an expense, and we want to subtract instead of add
#         await crud.account.update_by_id_and_field(db=db, id=expense.account_id, column='total_expenses', amount=-amount)

#     if expense_in.place_id:
#         place = await crud.place.get(db=db, id=expense_in.place_id)
#         if not place:
#             expense_in.place_id = expense.place_id

#     if expense_in.category_id:
#         category = await crud.category.get(db=db, id=expense_in.category_id)
#         if not category:
#             expense_in.category_id = expense.category_id

#     if expense_in.subcategory_id:
#         subcategory = await crud.subcategory.get(db=db, id=expense_in.subcategory_id)
#         if not subcategory:
#             expense_in.subcategory_id = expense.subcategory_id

#     expense = await crud.expense.update(db=db, db_obj=expense, obj_in=expense_in)

#     if expense_in.account_id:
#         await crud.account.update_by_id_and_field(db=db, id=expense_in.account_id, column='total_expenses', amount=expense.amount)

#     # Update the outcomes from the user's balance
#     await crud.user.update_balance(db=db, user_id=current_user.id, is_Expense=True, amount=-expense.amount)

#     return expense


# @router.delete("/{id}", response_model=schemas.DeletionResponse)
# async def delete_expense(
#         *,
#         db: AsyncSession = Depends(deps.async_get_db),
#         id: int,
#         current_user: models.User = Depends(deps.get_current_active_user),
# ) -> Any:
#     """
#     Delete an expense.
#     """
#     expense = await read_expense(db=db, id=id, current_user=current_user)
#     expense = await crud.expense.remove(db=db, id=id)

#     # Remove the expense from the user's balance
#     await crud.user.update_balance(db=db, user_id=current_user.id, is_Expense=True, amount=-expense.amount)

#     if expense.account_id:
#         # amount is negative because it's an expense, and we want to subtract instead of add
#         await crud.account.update_by_id_and_field(db=db, id=expense.account_id, column='total_expenses', amount=-expense.amount)


#     return schemas.DeletionResponse(message=f"Item {id} deleted")
#     return expense
