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
    accounts: Optional[List[int]] = None,
    categories: Optional[List[int]] = None,
    places: Optional[List[int]] = None,
) -> list[Union[Expense, Income, Transfer]]:
    
    limit = 100
    offset = (page - 1) * limit

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

    # --- Apply common filters to each subquery ---
    # (The start_date, end_date, search, and amount filters are the same as before)
    if start_date:
        expense_subquery = expense_subquery.where(Expense.date >= start_date)
        income_subquery = income_subquery.where(Income.date >= start_date)
        transfer_subquery = transfer_subquery.where(Transfer.date >= start_date)
    # ... other similar filters ...

    # --- THIS IS THE CORRECTED FILTERING LOGIC ---
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
    union_query = union_all(expense_subquery, income_subquery, transfer_subquery).cte("union_query")

    # Now, select from the UNION, sort, and paginate it
    paginated_ids_query = (
        select(union_query.c.id, union_query.c.type, union_query.c.date)
        .order_by(union_query.c.date.desc() if order == "desc" else union_query.c.date.asc())
        .offset(offset)
        .limit(limit)
    )

    paginated_results = (await db.execute(paginated_ids_query)).all()
    if not paginated_results:
        return []

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
            final_results[('expense', e.id)] = e

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
            final_results[('income', i.id)] = i

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
            final_results[('transfer', t.id)] = t
    
    # Sort the final hydrated objects based on the order from our paginated query
    sorted_transactions = [final_results[(r.type, r.id)] for r in paginated_results]

    return sorted_transactions