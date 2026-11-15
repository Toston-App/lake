"""Dashboard queries shared by v3 API routes and the chat agent."""

import asyncio
from dataclasses import dataclass
from datetime import date as date_type
from typing import Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, cast, func, select
from sqlalchemy import Date as SADate
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
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
from app.services.errors import DomainError, NotFoundError
from app.services.periods import (
    DateFilterType,
    get_previous_period_date_range,
    parse_date_range,
)
from app.utilities.redis import get_cached, store_cached


async def _require_account(
    db: AsyncSession, user: models.User, account_id: int | None
) -> None:
    """A period scoped to an account needs one of the user's own accounts."""
    if account_id is not None:
        account = await crud.account.get_by_id(db, owner_id=user.id, id=account_id)
        if not account:
            raise NotFoundError("Account not found")


def _period_conditions(
    model, user: models.User, start: date_type, end: date_type, account_id
) -> list:
    """The user's incomes or expenses (``model``) dated within start..end."""
    conditions = [
        model.owner_id == user.id,
        cast(model.date, SADate) >= start,
        cast(model.date, SADate) <= end,
    ]
    if account_id is not None:
        conditions.append(model.account_id == account_id)
    return conditions


async def fetch_period_summary(
    db: AsyncSession,
    user: models.User,
    date_filter_type: DateFilterType,
    date: str,
    account_id: Optional[int] = None,
) -> SummaryResponse:
    await _require_account(db, user, account_id)

    cache_date_key = f"{date}:acc:{account_id or 'all'}"
    cached = await get_cached(
        "summary", user.id, date_filter_type.value, cache_date_key
    )
    if cached:
        return SummaryResponse(**cached)

    date_range = parse_date_range(date_filter_type, date)
    income_conditions = _period_conditions(
        Income, user, date_range.start_date, date_range.end_date, account_id
    )
    expense_conditions = _period_conditions(
        Expense, user, date_range.start_date, date_range.end_date, account_id
    )

    period_income_result = await db.execute(
        select(func.coalesce(func.sum(Income.amount), 0.0)).where(
            and_(*income_conditions)
        )
    )
    period_expenses_result = await db.execute(
        select(func.coalesce(func.sum(Expense.amount), 0.0)).where(
            and_(*expense_conditions)
        )
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
    await _require_account(db, user, account_id)

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


# How many categories a comparison lists, biggest change first.
COMPARISON_CATEGORY_LIMIT = 20
UNCATEGORIZED = "Uncategorized"


@dataclass(frozen=True)
class _PeriodTotals:
    start: date_type
    end: date_type
    income: float
    # Expenses per category id (None for uncategorized).
    expenses_by_category: dict[int | None, float]

    @property
    def expenses(self) -> float:
        return round(sum(self.expenses_by_category.values()), 2)

    def as_dict(self) -> dict:
        return {
            "start_date": self.start.isoformat(),
            "end_date": self.end.isoformat(),
            "income": self.income,
            "expenses": self.expenses,
            "net": round(self.income - self.expenses, 2),
        }


def _change(current: float, previous: float) -> dict:
    delta = round(current - previous, 2)
    percent = round(delta / previous * 100, 1) if previous else None
    return {"amount": delta, "percent": percent}


async def _period_totals(
    db: AsyncSession,
    user: models.User,
    start: date_type,
    end: date_type,
    account_id: int | None,
) -> _PeriodTotals:
    income = await db.scalar(
        select(func.coalesce(func.sum(Income.amount), 0.0)).where(
            and_(*_period_conditions(Income, user, start, end, account_id))
        )
    )
    by_category = await db.execute(
        select(Expense.category_id, func.coalesce(func.sum(Expense.amount), 0.0))
        .where(and_(*_period_conditions(Expense, user, start, end, account_id)))
        .group_by(Expense.category_id)
    )
    return _PeriodTotals(
        start=start,
        end=end,
        income=round(float(income), 2),
        expenses_by_category={
            category_id: round(float(total), 2) for category_id, total in by_category
        },
    )


async def _category_names(
    db: AsyncSession, user: models.User, category_ids: set[int | None]
) -> dict[int, str]:
    ids = [cid for cid in category_ids if cid is not None]
    if not ids:
        return {}
    rows = await db.execute(
        select(models.Category.id, models.Category.name).where(
            models.Category.owner_id == user.id, models.Category.id.in_(ids)
        )
    )
    return dict(rows.all())


async def fetch_period_comparison(
    db: AsyncSession,
    user: models.User,
    date_filter_type: DateFilterType,
    date: str,
    compare_date_filter_type: DateFilterType | None = None,
    compare_date: str | None = None,
    account_id: int | None = None,
) -> dict:
    """Income, expenses and net of two periods, and spending by category.

    Without a comparison period, the main period is compared with the one
    just before it (the previous month for a month, and so on). The two
    periods are queried one after the other: they share one session.
    """
    if (compare_date_filter_type is None) != (compare_date is None):
        raise DomainError(
            "Pass both compare_date_filter_type and compare_date, or neither."
        )
    await _require_account(db, user, account_id)

    current_range = parse_date_range(date_filter_type, date)
    if compare_date_filter_type is not None:
        previous_range = parse_date_range(compare_date_filter_type, compare_date)
    else:
        previous_range = get_previous_period_date_range(date_filter_type, date)

    current = await _period_totals(
        db, user, current_range.start_date, current_range.end_date, account_id
    )
    previous = await _period_totals(
        db, user, previous_range.start_date, previous_range.end_date, account_id
    )
    category_ids = {*current.expenses_by_category, *previous.expenses_by_category}
    names = await _category_names(db, user, category_ids)

    categories = []
    for category_id in category_ids:
        now = current.expenses_by_category.get(category_id, 0.0)
        before = previous.expenses_by_category.get(category_id, 0.0)
        categories.append(
            {
                "category_id": category_id,
                "category": names.get(category_id, UNCATEGORIZED),
                "current": now,
                "previous": before,
                "change": _change(now, before),
            }
        )
    categories.sort(key=lambda row: abs(row["change"]["amount"]), reverse=True)

    period, compared_to = current.as_dict(), previous.as_dict()
    return {
        "period": period,
        "compared_to": compared_to,
        "change": {
            key: _change(period[key], compared_to[key])
            for key in ("income", "expenses", "net")
        },
        "expenses_by_category": categories[:COMPARISON_CATEGORY_LIMIT],
        "categories_truncated": len(categories) > COMPARISON_CATEGORY_LIMIT,
    }
