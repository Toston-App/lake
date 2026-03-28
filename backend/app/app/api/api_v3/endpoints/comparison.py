import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.api.deps import DateFilterType
from app.api.api_v3.utils import parse_date_range
from app.process_data.process import account_diff, accounts_total, get_df
from app.schemas.dashboard import ComparisonResponse
from app.utilities.redis import get_cached, store_cached
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.get("/{date_filter_type}/{date}", response_model=ComparisonResponse)
async def get_comparison(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: str = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve period-over-period comparison data.
    Returns account growth percentages comparing current vs past period.
    Cached in Redis with 7-day TTL, invalidated on writes.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={
            "type": "v3_comparison",
            "date_filter_type": date_filter_type.value,
            "date_param": date,
        },
    )

    # Check cache first
    cached = await get_cached(
        "comparison", current_user.id, date_filter_type.value, date
    )
    if cached:
        enrich_event(request, cache={"hit": True, "prefix": "comparison"})
        return cached

    enrich_event(request, cache={"hit": False, "prefix": "comparison"})

    date_range = parse_date_range(date_filter_type, date)

    with timed() as t_db:
        # Fetch current + past period incomes and expenses
        # Also need accounts, places, categories for get_df enrichment
        (
            incomes_actual,
            incomes_past,
            expenses_actual,
            expenses_past,
            accounts,
            places,
            categories,
        ) = await asyncio.gather(
            crud.income.get_multi_by_date(
                db=db,
                owner_id=current_user.id,
                start_date=date_range.start_date,
                end_date=date_range.end_date,
            ),
            crud.income.get_multi_by_date(
                db=db,
                owner_id=current_user.id,
                start_date=date_range.past_start_date,
                end_date=date_range.past_end_date,
            ),
            crud.expense.get_multi_by_date(
                db=db,
                owner_id=current_user.id,
                start_date=date_range.start_date,
                end_date=date_range.end_date,
            ),
            crud.expense.get_multi_by_date(
                db=db,
                owner_id=current_user.id,
                start_date=date_range.past_start_date,
                end_date=date_range.past_end_date,
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

    enrich_event(
        request,
        database={
            "operation": "v3_comparison_fetch",
            "duration_ms": t_db.ms,
        },
    )

    # Handle empty data
    if (
        not incomes_actual
        and not expenses_actual
        and not incomes_past
        and not expenses_past
    ):
        result = ComparisonResponse(accounts_growth={})
        await store_cached(
            "comparison",
            current_user.id,
            date_filter_type.value,
            date,
            result.model_dump(),
        )
        return result

    with timed() as t_processing:
        accounts_enc = jsonable_encoder(accounts)
        places_enc = jsonable_encoder(places)
        categories_enc = jsonable_encoder(categories)

        # Build empty transfer list for get_df (comparison doesn't use transfers)
        empty_transfers = []

        dfs = get_df(
            expenses=jsonable_encoder(expenses_actual),
            incomes=jsonable_encoder(incomes_actual),
            transfers=empty_transfers,
            accounts=accounts_enc,
            places=places_enc,
            categories=categories_enc,
        )
        past_dfs = get_df(
            expenses=jsonable_encoder(expenses_past),
            incomes=jsonable_encoder(incomes_past),
            transfers=empty_transfers,
            accounts=accounts_enc,
            places=places_enc,
            categories=categories_enc,
        )

        past_totals = accounts_total(
            incomes_df=past_dfs["incomes"],
            expenses_df=past_dfs["expenses"],
        )
        actual_totals = accounts_total(
            incomes_df=dfs["incomes"],
            expenses_df=dfs["expenses"],
        )
        growth = account_diff(past=past_totals, actual=actual_totals)

    enrich_event(
        request,
        performance={
            "processing_duration_ms": t_processing.ms,
        },
    )

    # account_diff returns int keys; schema expects str keys
    result = ComparisonResponse(
        accounts_growth={str(k): v for k, v in growth.items()}
    )

    # Store in cache
    await store_cached(
        "comparison",
        current_user.id,
        date_filter_type.value,
        date,
        result.model_dump(),
    )

    return result
