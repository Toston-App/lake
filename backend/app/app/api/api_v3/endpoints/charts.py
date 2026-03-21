import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.api.deps import DateFilterType
from app.api.api_v3.utils import parse_date_range
from app.process_data.process import (
    account_charts,
    categories_charts,
    get_df,
    income_vs_expense_chart,
    net_chart,
)
from app.schemas.dashboard import ChartsResponse
from app.utilities.redis import get_cached, store_cached
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.get("/{date_filter_type}/{date}", response_model=ChartsResponse)
async def get_charts(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: str = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve chart data for a date range.
    Returns transaction timeline, category drilldown, and per-account balance charts.
    Cached in Redis with 7-day TTL, invalidated on writes.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={
            "type": "v3_charts",
            "date_filter_type": date_filter_type.value,
            "date_param": date,
        },
    )

    # Check cache first
    cached = await get_cached(
        "charts", current_user.id, date_filter_type.value, date
    )
    if cached:
        enrich_event(request, cache={"hit": True, "prefix": "charts"})
        return cached

    enrich_event(request, cache={"hit": False, "prefix": "charts"})

    date_range = parse_date_range(date_filter_type, date)

    with timed() as t_db:
        # Fetch current-period data only (no past period — that's for comparison)
        incomes, expenses, transfers, accounts, places, categories = (
            await asyncio.gather(
                crud.income.get_multi_by_date(
                    db=db,
                    owner_id=current_user.id,
                    start_date=date_range.start_date,
                    end_date=date_range.end_date,
                ),
                crud.expense.get_multi_by_date(
                    db=db,
                    owner_id=current_user.id,
                    start_date=date_range.start_date,
                    end_date=date_range.end_date,
                ),
                crud.transfer.get_multi_by_date(
                    db=db,
                    owner_id=current_user.id,
                    start_date=date_range.start_date,
                    end_date=date_range.end_date,
                ),
                crud.account.get_multi_by_owner(
                    db=db, owner_id=current_user.id
                ),
                crud.place.get_multi_by_owner(
                    db=db, owner_id=current_user.id
                ),
                crud.category.get_multi_by_owner(
                    db=db, owner_id=current_user.id
                ),
            )
        )

    enrich_event(
        request,
        database={
            "operation": "v3_charts_fetch",
            "duration_ms": t_db.ms,
            "incomes_count": len(incomes),
            "expenses_count": len(expenses),
            "transfers_count": len(transfers),
        },
    )

    # Empty data — return empty charts
    if not incomes and not expenses:
        result = ChartsResponse(
            net=[],
            income_vs_expense=[],
            categories=[],
            accounts={},
        )
        await store_cached(
            "charts",
            current_user.id,
            date_filter_type.value,
            date,
            result.model_dump(),
        )
        return result

    with timed() as t_processing:
        dfs = get_df(
            expenses=jsonable_encoder(expenses),
            incomes=jsonable_encoder(incomes),
            transfers=jsonable_encoder(transfers),
            accounts=jsonable_encoder(accounts),
            places=jsonable_encoder(places),
            categories=jsonable_encoder(categories),
        )

        net_chart_data = net_chart(
            date_filter_type=date_filter_type,
            expenses_df=dfs["expenses"],
            incomes_df=dfs["incomes"],
        )
        income_vs_expense_chart_data = income_vs_expense_chart(
            date_filter_type=date_filter_type,
            expenses_df=dfs["expenses"],
            incomes_df=dfs["incomes"],
        )
        categories_chart = categories_charts(
            expenses_df=dfs["expenses"],
            incomes_df=dfs["incomes"],
        )
        account_chart = account_charts(
            incomes_df=dfs["incomes"],
            expenses_df=dfs["expenses"],
            transfers_df=dfs["transfers"],
        )

    enrich_event(
        request,
        performance={
            "processing_duration_ms": t_processing.ms,
            "charts_generated": 4,
        },
    )

    result = ChartsResponse(
        net=net_chart_data,
        income_vs_expense=income_vs_expense_chart_data,
        categories=categories_chart,
        accounts=account_chart,
    )

    # Store in cache
    await store_cached(
        "charts",
        current_user.id,
        date_filter_type.value,
        date,
        result.model_dump(),
    )

    return result
