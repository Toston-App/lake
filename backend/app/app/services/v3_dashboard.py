"""Dashboard queries shared by v3 API routes and the chat agent."""

import asyncio
from typing import Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, cast, func, select
from sqlalchemy import Date as SADate
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api.deps import DateFilterType
from app.api.api_v3.utils import get_previous_period_date_range, parse_date_range
from app.models.expense import Expense
from app.models.income import Income
from app.process_data.process import (
    categories_charts,
    get_df,
    income_vs_expense_chart,
    net_chart,
)
from app.process_data.utils import calculate_summary
from app.schemas.dashboard import Balance, ChartsResponse, SummaryResponse
from app.utilities.redis import get_cached, store_cached


async def fetch_period_summary(
    db: AsyncSession,
    user: models.User,
    date_filter_type: DateFilterType,
    date: str,
    account_id: Optional[int] = None,
) -> SummaryResponse:
    if account_id is not None:
        account = await crud.account.get_by_id(db, owner_id=user.id, id=account_id)
        if not account:
            raise ValueError("Account not found")

    cache_date_key = f"{date}:acc:{account_id or 'all'}"
    cached = await get_cached(
        "summary", user.id, date_filter_type.value, cache_date_key
    )
    if cached:
        return SummaryResponse(**cached)

    date_range = parse_date_range(date_filter_type, date)
    income_conditions = [
        Income.owner_id == user.id,
        cast(Income.date, SADate) >= date_range.start_date,
        cast(Income.date, SADate) <= date_range.end_date,
    ]
    expense_conditions = [
        Expense.owner_id == user.id,
        cast(Expense.date, SADate) >= date_range.start_date,
        cast(Expense.date, SADate) <= date_range.end_date,
    ]
    if account_id is not None:
        income_conditions.append(Income.account_id == account_id)
        expense_conditions.append(Expense.account_id == account_id)

    period_income_result = await db.execute(
        select(func.coalesce(func.sum(Income.amount), 0.0)).where(and_(*income_conditions))
    )
    period_expenses_result = await db.execute(
        select(func.coalesce(func.sum(Expense.amount), 0.0)).where(and_(*expense_conditions))
    )

    period_income = round(float(period_income_result.scalar()), 2)
    period_expenses = round(float(period_expenses_result.scalar()), 2)

    if account_id is not None:
        account = await crud.account.get_by_id(db, owner_id=user.id, id=account_id)
        balance = Balance(
            total=round(account.current_balance, 2),
            income=round(account.total_incomes, 2),
            outcome=round(account.total_expenses, 2),
        )
    else:
        balance = Balance(
            total=round(user.balance_total, 2),
            income=round(user.balance_income, 2),
            outcome=round(user.balance_outcome, 2),
        )

    result = SummaryResponse(
        country=user.country,
        balance=balance,
        period_income=period_income,
        period_expenses=period_expenses,
        period_net=round(period_income - period_expenses, 2),
    )

    await store_cached(
        "summary",
        user.id,
        date_filter_type.value,
        cache_date_key,
        result.model_dump(),
    )
    return result


async def fetch_period_charts(
    db: AsyncSession,
    user: models.User,
    date_filter_type: DateFilterType,
    date: str,
    account_id: Optional[int] = None,
) -> ChartsResponse:
    if account_id is not None:
        account = await crud.account.get_by_id(db, owner_id=user.id, id=account_id)
        if not account:
            raise ValueError("Account not found")

    cache_date_key = f"{date}:acc:{account_id or 'all'}"
    cached = await get_cached("charts", user.id, date_filter_type.value, cache_date_key)
    if cached:
        return ChartsResponse(**cached)

    date_range = parse_date_range(date_filter_type, date)
    prev_date_range = get_previous_period_date_range(date_filter_type, date)

    (
        incomes,
        expenses,
        transfers,
        accounts,
        places,
        categories,
        prev_incomes,
        prev_expenses,
    ) = await asyncio.gather(
        crud.income.get_multi_by_date(
            db=db,
            owner_id=user.id,
            start_date=date_range.start_date,
            end_date=date_range.end_date,
            account_id=account_id,
        ),
        crud.expense.get_multi_by_date(
            db=db,
            owner_id=user.id,
            start_date=date_range.start_date,
            end_date=date_range.end_date,
            account_id=account_id,
        ),
        crud.transfer.get_multi_by_date(
            db=db,
            owner_id=user.id,
            start_date=date_range.start_date,
            end_date=date_range.end_date,
            account_id=account_id,
        ),
        crud.account.get_multi_by_owner(db=db, owner_id=user.id),
        crud.place.get_multi_by_owner(db=db, owner_id=user.id),
        crud.category.get_multi_by_owner(db=db, owner_id=user.id),
        crud.income.get_multi_by_date(
            db=db,
            owner_id=user.id,
            start_date=prev_date_range.start_date,
            end_date=prev_date_range.end_date,
            account_id=account_id,
        ),
        crud.expense.get_multi_by_date(
            db=db,
            owner_id=user.id,
            start_date=prev_date_range.start_date,
            end_date=prev_date_range.end_date,
            account_id=account_id,
        ),
    )

    if not incomes and not expenses:
        result = ChartsResponse(
            net=[],
            income_vs_expense=[],
            categories=[],
            accounts={},
        )
        await store_cached(
            "charts",
            user.id,
            date_filter_type.value,
            cache_date_key,
            result.model_dump(),
        )
        return result

    dfs = get_df(
        expenses=jsonable_encoder(expenses),
        incomes=jsonable_encoder(incomes),
        transfers=jsonable_encoder(transfers),
        accounts=jsonable_encoder(accounts),
        places=jsonable_encoder(places),
        categories=jsonable_encoder(categories),
    )
    prev_dfs = get_df(
        expenses=jsonable_encoder(prev_expenses),
        incomes=jsonable_encoder(prev_incomes),
        transfers=[],
        accounts=jsonable_encoder(accounts),
        places=jsonable_encoder(places),
        categories=jsonable_encoder(categories),
    )

    net_chart_data = net_chart(
        date_filter_type=date_filter_type,
        expenses_df=dfs["expenses"],
        incomes_df=dfs["incomes"],
    )

    if prev_incomes or prev_expenses:
        prev_net_chart_data = net_chart(
            date_filter_type=date_filter_type,
            expenses_df=prev_dfs["expenses"],
            incomes_df=prev_dfs["incomes"],
        )
        previous_total = sum(prev_net_chart_data["series"][0]["data"])
    else:
        previous_total = 0.0

    current_total = sum(net_chart_data["series"][0]["data"])
    net_chart_data["summary"] = calculate_summary(current_total, previous_total)

    income_vs_expense_chart_data = income_vs_expense_chart(
        date_filter_type=date_filter_type,
        expenses_df=dfs["expenses"],
        incomes_df=dfs["incomes"],
    )
    categories_chart = categories_charts(
        expenses_df=dfs["expenses"],
        incomes_df=dfs["incomes"],
    )

    result = ChartsResponse(
        net=net_chart_data,
        income_vs_expense=income_vs_expense_chart_data,
        categories=categories_chart,
    )

    await store_cached(
        "charts",
        user.id,
        date_filter_type.value,
        cache_date_key,
        result.model_dump(),
    )
    return result
