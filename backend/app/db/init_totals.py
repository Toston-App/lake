import asyncio
import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session
from app.models.category import Category
from app.models.expense import Expense
from app.models.income import Income
from app.models.subcategory import Subcategory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Note: IA sucks, this is getting the expenses and incomes from the database twice instead of once but I don't expect to much data so it should be fine. If you have a lot of data you should consider optimizing this.

async def update_category_totals(db: AsyncSession):
    """Update category totals from existing expenses and incomes"""
    # First, reset all totals to 0
    categories = await db.execute(select(Category))
    categories = categories.scalars().all()

    for category in categories:
        category.total = 0.0

    await db.commit()
    logger.info(f"Reset totals for {len(categories)} categories")

    # Now process expenses
    expenses = await db.execute(select(Expense).where(Expense.category_id.isnot(None)))
    expenses = expenses.scalars().all()

    category_totals = {}
    for expense in expenses:
        if expense.category_id not in category_totals:
            category_totals[expense.category_id] = 0

        category_totals[expense.category_id] += expense.amount

    # Process incomes for income categories (via subcategories)
    # Income doesn't have direct category_id, need to join through subcategory
    income_query = select(
        Subcategory.category_id,
        func.sum(Income.amount).label('total')
    ).join(
        Income, Income.subcategory_id == Subcategory.id
    ).where(
        Subcategory.category_id.isnot(None),
        Income.subcategory_id.isnot(None)
    ).group_by(
        Subcategory.category_id
    )

    result = await db.execute(income_query)
    income_totals_by_category = result.all()

    # Add income totals to category totals
    for category_id, total in income_totals_by_category:
        if category_id not in category_totals:
            category_totals[category_id] = 0

        category_totals[category_id] += total

    # Update categories
    for category_id, total in category_totals.items():
        category = await db.get(Category, category_id)
        if category:
            category.total = total

    await db.commit()
    logger.info(f"Updated totals for {len(category_totals)} categories")


async def update_subcategory_totals(db: AsyncSession):
    """Update subcategory totals from existing expenses and incomes"""
    # First, reset all totals to 0
    subcategories = await db.execute(select(Subcategory))
    subcategories = subcategories.scalars().all()

    for subcategory in subcategories:
        subcategory.total = 0.0

    await db.commit()
    logger.info(f"Reset totals for {len(subcategories)} subcategories")

    # Process expenses
    expenses = await db.execute(select(Expense).where(Expense.subcategory_id.isnot(None)))
    expenses = expenses.scalars().all()

    subcategory_totals = {}
    for expense in expenses:
        if expense.subcategory_id not in subcategory_totals:
            subcategory_totals[expense.subcategory_id] = 0

        subcategory_totals[expense.subcategory_id] += expense.amount

    # Process incomes
    incomes = await db.execute(select(Income).where(Income.subcategory_id.isnot(None)))
    incomes = incomes.scalars().all()

    for income in incomes:
        if income.subcategory_id not in subcategory_totals:
            subcategory_totals[income.subcategory_id] = 0

        subcategory_totals[income.subcategory_id] += income.amount

    # Update subcategories
    for subcategory_id, total in subcategory_totals.items():
        subcategory = await db.get(Subcategory, subcategory_id)
        if subcategory:
            subcategory.total = total

    await db.commit()
    logger.info(f"Updated totals for {len(subcategory_totals)} subcategories")


async def main():
    logger.info("Starting database totals update")
    start_time = datetime.now()

    async with async_session() as session:
        # Update category totals
        await update_category_totals(session)

        # Update subcategory totals
        await update_subcategory_totals(session)

    elapsed_time = datetime.now() - start_time
    logger.info(f"Finished updating totals in {elapsed_time}")


if __name__ == "__main__":
    asyncio.run(main())
