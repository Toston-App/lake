from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.api import deps
from app.api.deps import DateFilterType
from app.schemas.dashboard import SummaryResponse
from app.services.v3_dashboard import fetch_period_summary
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.get("/{date_filter_type}/{date}", response_model=SummaryResponse)
async def get_summary(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: str = None,
    account_id: Optional[int] = Query(None, description="Filter by account ID"),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve high-level financial summary for a date range.
    Returns user balance, period income/expenses totals, and net.
    Optionally filtered by a specific account.
    Cached in Redis with 7-day TTL, invalidated on writes.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={
            "type": "v3_summary",
            "date_filter_type": date_filter_type.value,
            "date_param": date,
            "account_id": account_id,
        },
    )

    try:
        with timed() as t:
            result = await fetch_period_summary(
                db, current_user, date_filter_type, date, account_id
            )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    enrich_event(
        request,
        database={
            "operation": "v3_summary",
            "duration_ms": t.ms,
        },
        response={
            "period_income": result.period_income,
            "period_expenses": result.period_expenses,
            "period_net": result.period_net,
        },
    )

    return result
