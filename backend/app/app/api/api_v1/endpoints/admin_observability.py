"""Admin observability dashboard endpoints (superuser-only)."""

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.schemas.observability import (
    ModelStat,
    ObservabilitySummary,
    TimeseriesPoint,
    ToolStat,
    TurnDetail,
    TurnsListResponse,
)

router = APIRouter()


@router.get("/summary", response_model=ObservabilitySummary)
async def observability_summary(
    db: AsyncSession = Depends(deps.async_get_db),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    return await crud.observability.get_summary(db, start=start, end=end)


@router.get("/cost-timeseries", response_model=list[TimeseriesPoint])
async def observability_cost_timeseries(
    db: AsyncSession = Depends(deps.async_get_db),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    bucket: Literal["hour", "day", "week", "month"] = Query("day"),
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    return await crud.observability.get_cost_timeseries(
        db, start=start, end=end, bucket=bucket
    )


@router.get("/tools", response_model=list[ToolStat])
async def observability_tools(
    db: AsyncSession = Depends(deps.async_get_db),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    return await crud.observability.get_tool_stats(db, start=start, end=end)


@router.get("/models", response_model=list[ModelStat])
async def observability_models(
    db: AsyncSession = Depends(deps.async_get_db),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    return await crud.observability.get_model_stats(db, start=start, end=end)


@router.get("/turns", response_model=TurnsListResponse)
async def observability_turns(
    db: AsyncSession = Depends(deps.async_get_db),
    status: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    conversation_id: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    items = await crud.observability.get_turns(
        db,
        start=start,
        end=end,
        status=status,
        user_id=user_id,
        conversation_id=conversation_id,
        model=model,
        search=search,
        skip=skip,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/turns/{turn_id}", response_model=TurnDetail)
async def observability_turn_detail(
    turn_id: int,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    detail = await crud.observability.get_turn_detail(db, turn_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    return detail
