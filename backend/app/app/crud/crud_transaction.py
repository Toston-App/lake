# TODO: pagination sucks, implement a better way. (thats why I added .limit to all queries. remove it). Check fastapi_paginator

from datetime import date
from typing import Optional, Union

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from app.models.category import Category
from app.models.expense import Expense
from app.models.income import Income
from app.models.subcategory import Subcategory
from app.models.transfer import Transfer


async def get_multi_by_owner_with_filters(
    db: AsyncSession,
    *,
    owner_id: int,
    page: int = 1,
    order: str = "desc",
    search: Optional[str] = None,
    amount: Optional[float] = None,
    amount_operator: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    accounts: Optional[list[int]] = None,
    categories: Optional[list[int]] = None,
    places: Optional[list[int]] = None,
) -> list[Union[Expense, Income, Transfer]]:
    print("🚀 ~ owner_id:", owner_id)
    print("🚀 ~ places:", places)
    print("🚀 ~ categories:", categories)
    print("🚀 ~ accounts:", accounts)
    print("🚀 ~ end_date:", end_date)
    print("🚀 ~ start_date:", start_date)
    print("🚀 ~ amount_operator:", amount_operator)
    print("🚀 ~ amount:", amount)
    print("🚀 ~ search:", search)
    print("🚀 ~ order:", order)
    # Starting with empty lists for each transaction type
    all_transactions = []
    limit = 100
    offset = (page - 1) * limit

    # --- Expenses Query ---
    expense_query = (
        select(Expense)
        .options(
            joinedload(Expense.account),
            joinedload(Expense.category).selectinload(Category.subcategories),
            joinedload(Expense.subcategory),
            joinedload(Expense.place),
        )
        .where(Expense.owner_id == owner_id)
        .limit(limit)
    )

    # --- Incomes Query ---
    income_query = (
        select(Income)
        .options(
            joinedload(Income.account),
            joinedload(Income.subcategory)
             .joinedload(Subcategory.category)
             .selectinload(Category.subcategories),
            joinedload(Income.place),
        )
        .where(Income.owner_id == owner_id)
        .limit(limit)
    )

    # --- Transfers Query ---
    transfer_query = (
        select(Transfer)
        .options(
            joinedload(Transfer.account_from),
            joinedload(Transfer.account_to),
        )
        .where(Transfer.owner_id == owner_id)
        .limit(limit)
    )

    # Common filters
    if start_date:
        expense_query = expense_query.where(Expense.date >= start_date)
        income_query = income_query.where(Income.date >= start_date)
        transfer_query = transfer_query.where(Transfer.date >= start_date)
    if end_date:
        expense_query = expense_query.where(Expense.date <= end_date)
        income_query = income_query.where(Income.date <= end_date)
        transfer_query = transfer_query.where(Transfer.date <= end_date)

    if search:
        search_filter = f"%{search}%"
        expense_query = expense_query.where(Expense.description.ilike(search_filter))
        income_query = income_query.where(Income.description.ilike(search_filter))
        transfer_query = transfer_query.where(
            Transfer.description.ilike(search_filter)
        )

    if amount and amount_operator:
        if amount_operator == "equal":
            expense_query = expense_query.where(Expense.amount == amount)
            income_query = income_query.where(Income.amount == amount)
            transfer_query = transfer_query.where(Transfer.amount == amount)
        elif amount_operator == "less":
            expense_query = expense_query.where(Expense.amount < amount)
            income_query = income_query.where(Income.amount < amount)
            transfer_query = transfer_query.where(Transfer.amount < amount)
        elif amount_operator == "greater":
            expense_query = expense_query.where(Expense.amount > amount)
            income_query = income_query.where(Income.amount > amount)
            transfer_query = transfer_query.where(Transfer.amount > amount)

    if accounts:
        expense_query = expense_query.where(Expense.account_id.in_(accounts))
        income_query = income_query.where(Income.account_id.in_(accounts))
        transfer_query = transfer_query.where(
            or_(Transfer.from_acc.in_(accounts), Transfer.to_acc.in_(accounts))
        )

    if places:
        expense_query = expense_query.where(Expense.place_id.in_(places))
        income_query = income_query.where(Income.place_id.in_(places))

    if categories:
        # For expenses, filter by category_id
        expense_query = expense_query.where(Expense.category_id.in_(categories))
        # For incomes, filter by the category of the subcategory
        income_query = income_query.join(
            Subcategory, Income.subcategory_id == Subcategory.id
        ).where(Subcategory.category_id.in_(categories))

    # Execute queries
    expense_results = (await db.execute(expense_query)).scalars().all()
    print("🚀 ~ expense_query:", expense_query)
    income_results = (await db.execute(income_query)).scalars().all()
    transfer_results = (await db.execute(transfer_query)).scalars().all()

    all_transactions.extend(expense_results)
    all_transactions.extend(income_results)
    all_transactions.extend(transfer_results)

    # Sort
    if order == "desc":
        all_transactions.sort(key=lambda x: x.date, reverse=True)
    else:
        all_transactions.sort(key=lambda x: x.date)

    # Paginate
    return all_transactions
