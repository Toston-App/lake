from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.services import balance_adjustments as balance_adjustment_service
from app.services.errors import ServiceError, raise_http
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.post("", response_model=schemas.BalanceAdjustment)
async def create_balance_adjustment(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    adjustment_in: schemas.BalanceAdjustmentCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create a new balance adjustment and update the account's current balance.

    This endpoint:
    1. Verifies the account exists and belongs to the user
    2. Records the old balance
    3. Creates the balance adjustment record
    4. Updates the account's current_balance to the new_balance
    5. Updates the user's balance_total by the adjustment amount
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "create_balance_adjustment",
            "account_id": adjustment_in.account_id,
            "new_balance": float(adjustment_in.new_balance),
        },
    )

    try:
        with timed() as t:
            adjustment = await balance_adjustment_service.create_balance_adjustment(
                db, current_user, adjustment_in
            )
    except ServiceError as exc:
        raise_http(exc)

    enrich_event(
        request,
        database={"operation": "create_balance_adjustment", "duration_ms": t.ms, "success": True},
        transaction={
            "old_balance": float(adjustment.old_balance),
            "new_balance": float(adjustment_in.new_balance),
            "adjustment_amount": float(adjustment.adjustment_amount),
        },
    )

    return adjustment


@router.get("/account/{account_id}", response_model=list[schemas.BalanceAdjustment])
async def read_balance_adjustments_by_account(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    account_id: int,
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve balance adjustment history for a specific account.
    Returns adjustments ordered by date (newest first).
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "list_adjustments_by_account", "account_id": account_id, "skip": skip, "limit": limit},
    )

    try:
        adjustments = await balance_adjustment_service.list_balance_adjustments_by_account(
            db, current_user, account_id, skip=skip, limit=limit
        )
    except ServiceError as exc:
        raise_http(exc)

    enrich_event(request, database={"operation": "list_adjustments_by_account", "results_count": len(adjustments)})
    return adjustments


@router.get("/{id}", response_model=schemas.BalanceAdjustment)
async def read_balance_adjustment(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a specific balance adjustment by ID.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_adjustment_by_id", "adjustment_id": id},
    )

    try:
        return await balance_adjustment_service.get_balance_adjustment(
            db, current_user, id
        )
    except ServiceError as exc:
        raise_http(exc)


@router.get("", response_model=list[schemas.BalanceAdjustment])
async def read_balance_adjustments(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve all balance adjustments made by the current user.
    Superusers can see all adjustments.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "list_all_adjustments", "skip": skip, "limit": limit},
    )

    adjustments = await balance_adjustment_service.list_balance_adjustments(
        db, current_user, skip=skip, limit=limit
    )

    enrich_event(request, database={"operation": "list_all_adjustments", "results_count": len(adjustments)})
    return adjustments
