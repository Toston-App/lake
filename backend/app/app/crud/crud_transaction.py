from datetime import date
from typing import List, Optional, Union

from sqlalchemy import and_, or_, union_all, select, literal_column, null
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models.account import Account
from app.models.category import Category
from app.models.expense import Expense
from app.models.income import Income
from app.models.place import Place
from app.models.subcategory import Subcategory
from app.models.transfer import Transfer
from app.schemas.transaction import (
    AmountOperator,
    OrderDirection,
    TransactionType,
    Transaction,
    ExpenseTransaction,
    IncomeTransaction,
    TransferTransaction,
)
from fastapi_pagination import Page, Params
from fastapi_pagination.ext.sqlalchemy import paginate


async def get_multi_by_owner_with_filters(
    db: AsyncSession,
    *,
    owner_id: int,
    order: OrderDirection = OrderDirection.desc,
    search: Optional[str] = None,
    amount: Optional[float] = None,
    amount_operator: Optional[AmountOperator] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    accounts: Optional[List[int]] = None,
    categories: Optional[List[int]] = None,
    places: Optional[List[int]] = None,
    transaction_type: Optional[List[TransactionType]] = None,
    page: Optional[int] = None,
    size: Optional[int] = None,
) -> Page[Transaction]:
    print("🚀 ~ db:", db)
    print("🚀 ~ owner_id:", owner_id)
    print("🚀 ~ transaction_type:", transaction_type)
    print("🚀 ~ places:", places)
    print("🚀 ~ categories:", categories)
    print("🚀 ~ accounts:", accounts)
    print("🚀 ~ end_date:", end_date)
    print("🚀 ~ start_date:", start_date)
    print("🚀 ~ amount_operator:", amount_operator)
    print("🚀 ~ amount:", amount)
    print("🚀 ~ search:", search)
    print("🚀 ~ order:", order)
    # =========================================================================
    # PHASE 1: Build a UNION query to get the correct IDs for the page
    # =========================================================================

    # Define common columns for the UNION. The names are arbitrary but must be consistent.
    # We must use null() for columns that don't exist in a particular table.
    expense_subquery = (
        select(
            Expense.id.label("id"),
            literal_column("'expense'").label("type"),
            Expense.date.label("date"),
        )
        .where(Expense.owner_id == owner_id)
    )

    income_subquery = (
        select(
            Income.id.label("id"),
            literal_column("'income'").label("type"),
            Income.date.label("date"),
        )
        # We still need this join for filtering by category later
        .join(Subcategory, Income.subcategory_id == Subcategory.id, isouter=True)
        .where(Income.owner_id == owner_id)
    )

    transfer_subquery = (
        select(
            Transfer.id.label("id"),
            literal_column("'transfer'").label("type"),
            Transfer.date.label("date"),
        )
        .where(Transfer.owner_id == owner_id)
    )

    if start_date:
        expense_subquery = expense_subquery.where(Expense.date >= start_date)
        income_subquery = income_subquery.where(Income.date >= start_date)
        transfer_subquery = transfer_subquery.where(Transfer.date >= start_date)
    if end_date:
        expense_subquery = expense_subquery.where(Expense.date <= end_date)
        income_subquery = income_subquery.where(Income.date <= end_date)
        transfer_subquery = transfer_subquery.where(Transfer.date <= end_date)

    if search:
        search_term = f"%{search}%"
        expense_subquery = expense_subquery.where(Expense.description.ilike(search_term))
        income_subquery = income_subquery.where(Income.description.ilike(search_term))
        transfer_subquery = transfer_subquery.where(Transfer.description.ilike(search_term))

    if amount and amount_operator:
        if amount_operator == AmountOperator.equal:
            expense_subquery = expense_subquery.where(Expense.amount == amount)
            income_subquery = income_subquery.where(Income.amount == amount)
            transfer_subquery = transfer_subquery.where(Transfer.amount == amount)
        elif amount_operator == AmountOperator.less:
            expense_subquery = expense_subquery.where(Expense.amount < amount)
            income_subquery = income_subquery.where(Income.amount < amount)
            transfer_subquery = transfer_subquery.where(Transfer.amount < amount)
        elif amount_operator == AmountOperator.greater:
            expense_subquery = expense_subquery.where(Expense.amount > amount)
            income_subquery = income_subquery.where(Income.amount > amount)
            transfer_subquery = transfer_subquery.where(Transfer.amount > amount)

    if accounts:
        expense_subquery = expense_subquery.where(Expense.account_id.in_(accounts))
        income_subquery = income_subquery.where(Income.account_id.in_(accounts))
        # Apply the OR condition in the WHERE clause, not the SELECT statement
        transfer_subquery = transfer_subquery.where(
            or_(Transfer.from_acc.in_(accounts), Transfer.to_acc.in_(accounts))
        )

    if places:
        expense_subquery = expense_subquery.where(Expense.place_id.in_(places))
        income_subquery = income_subquery.where(Income.place_id.in_(places))
        # Transfers don't have places, so filter them out if a place is selected
        transfer_subquery = transfer_subquery.where(literal_column("1=0")) 

    if categories:
        expense_subquery = expense_subquery.where(Expense.category_id.in_(categories))
        income_subquery = income_subquery.where(Subcategory.category_id.in_(categories))
        # Transfers don't have categories, so filter them out
        transfer_subquery = transfer_subquery.where(literal_column("1=0"))

    # Combine the subqueries into a single UNION
    # The selected columns are now simpler and consistent
    subqueries = []
    if not transaction_type or TransactionType.expense in transaction_type:
        subqueries.append(expense_subquery)
    if not transaction_type or TransactionType.income in transaction_type:
        subqueries.append(income_subquery)
    if not transaction_type or TransactionType.transfer in transaction_type:
        subqueries.append(transfer_subquery)

    if not subqueries:
        return Page(items=[], total=0, page=1, size=0)

    union_query = union_all(*subqueries).cte("union_query")

    # Now, select from the UNION, sort, and paginate it
    paginated_ids_query = (
        select(union_query.c.id, union_query.c.type, union_query.c.date)
        .order_by(union_query.c.date.desc() if order == OrderDirection.desc else union_query.c.date.asc())
    )

    async def _hydrate_transactions(paginated_results: list) -> list[Transaction]:
        # =========================================================================
        # PHASE 2: "Hydrate" the IDs into full SQLAlchemy objects
        # =========================================================================

        # Separate the IDs by type
        expense_ids = [r.id for r in paginated_results if r.type == 'expense']
        income_ids = [r.id for r in paginated_results if r.type == 'income']
        transfer_ids = [r.id for r in paginated_results if r.type == 'transfer']

        final_results = {}

        # Fetch all the necessary objects in targeted queries
        if expense_ids:
            expenses = (await db.execute(
                select(Expense)
                .options(
                    joinedload(Expense.account),
                    joinedload(Expense.category).selectinload(Category.subcategories),
                    joinedload(Expense.subcategory),
                    joinedload(Expense.place),
                )
                .where(Expense.id.in_(expense_ids))
            )).scalars().all()
            for e in expenses:
                final_results[('expense', e.id)] = ExpenseTransaction.model_validate(e)

        if income_ids:
            incomes = (await db.execute(
                select(Income)
                .options(
                    joinedload(Income.account),
                    joinedload(Income.subcategory).joinedload(Subcategory.category).selectinload(Category.subcategories),
                    joinedload(Income.place),
                )
                .where(Income.id.in_(income_ids))
            )).scalars().all()
            for i in incomes:
                final_results[('income', i.id)] = IncomeTransaction.model_validate(i)

        if transfer_ids:
            transfers = (await db.execute(
                select(Transfer)
                .options(
                    joinedload(Transfer.account_from),
                    joinedload(Transfer.account_to),
                )
                .where(Transfer.id.in_(transfer_ids))
            )).scalars().all()
            for t in transfers:
                final_results[('transfer', t.id)] = TransferTransaction.model_validate(t)

        # Sort the final hydrated objects based on the order from our paginated query
        return [final_results[(r.type, r.id)] for r in paginated_results]

    print("🚀 ~ before res")
    params = Params(page=page, size=size) if page and size else None
    res = await paginate(db, paginated_ids_query, params=params, transformer=_hydrate_transactions)
    print("🚀 ~ res:", res)
    return res
