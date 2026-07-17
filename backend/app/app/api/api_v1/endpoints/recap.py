"""
Recap API Endpoints - 2025 Annual Financial Recap

Endpoints for generating, retrieving, and managing user financial recaps.
Recaps are cached in Redis for fast retrieval.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.api import deps
from app.schemas.recap import (
    RecapStatus,
    RecapStatusResponse,
    UserRecap,
)
from app.utilities.redis import (
    get_recap,
    get_recap_status,
)
from app.utilities.wide_events import enrich_event

router = APIRouter()


@router.get("/{year}", response_model=UserRecap | RecapStatusResponse)
async def get_user_recap(
    request: Request,
    year: int,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve the user's annual financial recap.

    Returns the cached recap if completed, otherwise returns the current status.
    If no recap exists, returns 404.
    """
    year = 2025

    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_recap", "year": year},
    )

    cached_recap = await get_recap(current_user.id, year)
    if cached_recap:
        enrich_event(request, recap={"outcome": "cache_hit"})
        return UserRecap(**cached_recap)

    status_data = await get_recap_status(current_user.id, year)
    if status_data:
        enrich_event(request, recap={"outcome": "status_found", "status": status_data["status"]})
        return RecapStatusResponse(
            user_id=current_user.id,
            year=year,
            status=RecapStatus(status_data["status"]),
            message=status_data.get("message")
        )

    enrich_event(request, recap={"outcome": "not_found"})
    raise HTTPException(
        status_code=404,
        detail=f"No recap found for year {year}. Use POST /recap/{year}/generate to create one."
    )


@router.get("/{year}/status", response_model=RecapStatusResponse)
async def get_recap_generation_status(
    request: Request,
    year: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get the current status of recap generation for a specific year.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_recap_status", "year": year},
    )

    if year < 2020 or year > 2030:
        raise HTTPException(
            status_code=400,
            detail="Year must be between 2020 and 2030"
        )

    cached_recap = await get_recap(current_user.id, year)
    if cached_recap:
        enrich_event(request, recap={"outcome": "completed"})
        return RecapStatusResponse(
            user_id=current_user.id,
            year=year,
            status=RecapStatus.completed,
            message="Recap is available"
        )

    status_data = await get_recap_status(current_user.id, year)
    if status_data:
        enrich_event(request, recap={"outcome": "in_progress", "status": status_data["status"]})
        return RecapStatusResponse(
            user_id=current_user.id,
            year=year,
            status=RecapStatus(status_data["status"]),
            message=status_data.get("message")
        )

    enrich_event(request, recap={"outcome": "pending"})
    return RecapStatusResponse(
        user_id=current_user.id,
        year=year,
        status=RecapStatus.pending,
        message="No recap has been generated yet"
    )
