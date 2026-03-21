from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, cast, func, select
from sqlalchemy import Date as SADate
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.api import deps
from app.api.deps import DateFilterType
from app.api.api_v3.utils import parse_date_range
from app.models.expense import Expense
from app.models.income import Income
from app.schemas.dashboard import Balance, SummaryResponse
from app.utilities.redis import get_cached, store_cached
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.get("/{date_filter_type}/{date}", response_model=SummaryResponse)
async def get_summary(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: str = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve high-level financial summary for a date range.
    Returns user balance, period income/expenses totals, and net.
    Cached in Redis with 7-day TTL, invalidated on writes.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={
            "type": "v3_summary",
            "date_filter_type": date_filter_type.value,
            "date_param": date,
        },
    )

    # Check cache first
    cached = await get_cached(
        "summary", current_user.id, date_filter_type.value, date
    )
    if cached:
        enrich_event(request, cache={"hit": True, "prefix": "summary"})
        return cached

    enrich_event(request, cache={"hit": False, "prefix": "summary"})

    date_range = parse_date_range(date_filter_type, date)

    with timed() as t:
        period_income_result = await db.execute(
            select(func.coalesce(func.sum(Income.amount), 0.0)).where(
                and_(
                    Income.owner_id == current_user.id,
                    cast(Income.date, SADate) >= date_range.start_date,
                    cast(Income.date, SADate) <= date_range.end_date,
                )
            )
        )
        period_expenses_result = await db.execute(
            select(func.coalesce(func.sum(Expense.amount), 0.0)).where(
                and_(
                    Expense.owner_id == current_user.id,
                    cast(Expense.date, SADate) >= date_range.start_date,
                    cast(Expense.date, SADate) <= date_range.end_date,
                )
            )
        )

    period_income = round(float(period_income_result.scalar()), 2)
    period_expenses = round(float(period_expenses_result.scalar()), 2)

    enrich_event(
        request,
        database={
            "operation": "v3_summary",
            "duration_ms": t.ms,
        },
        response={
            "period_income": period_income,
            "period_expenses": period_expenses,
            "period_net": round(period_income - period_expenses, 2),
        },
    )

    result = SummaryResponse(
        currency=current_user.country,
        language=current_user.country,
        balance=Balance(
            total=round(current_user.balance_total, 2),
            income=round(current_user.balance_income, 2),
            outcome=round(current_user.balance_outcome, 2),
        ),
        period_income=period_income,
        period_expenses=period_expenses,
        period_net=round(period_income - period_expenses, 2),
    )

    # Store in cache
    await store_cached(
        "summary",
        current_user.id,
        date_filter_type.value,
        date,
        result.model_dump(),
    )

    return result
