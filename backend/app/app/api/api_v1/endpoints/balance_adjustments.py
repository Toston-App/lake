from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
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

    account = await crud.account.get(db=db, id=adjustment_in.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not crud.user.is_superuser(current_user) and (
        account.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")

    old_balance = account.current_balance

    with timed() as t:
        adjustment = await crud.balance_adjustment.create_with_user(
            db=db,
            obj_in=adjustment_in,
            owner_id=current_user.id,
            old_balance=old_balance,
        )
        # Update the account's current_balance
        current_account_data = jsonable_encoder(account)
        account_update = schemas.AccountUpdate(**current_account_data)
        account_update.current_balance = adjustment_in.new_balance

        await crud.account.update(db=db, db_obj=account, obj_in=account_update)
        # Update the user's balance_total by the adjustment amount
        adjustment_amount = adjustment_in.new_balance - old_balance
        current_user_data = jsonable_encoder(current_user)
        user_update = schemas.UserUpdate(**current_user_data)
        user_update.balance_total = current_user.balance_total + adjustment_amount

        await crud.user.update(db=db, db_obj=current_user, obj_in=user_update)

    enrich_event(
        request,
        database={"operation": "create_balance_adjustment", "duration_ms": t.ms, "success": True},
        transaction={
            "old_balance": float(old_balance),
            "new_balance": float(adjustment_in.new_balance),
            "adjustment_amount": float(adjustment_in.new_balance - old_balance),
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

    account = await crud.account.get(db=db, id=account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not crud.user.is_superuser(current_user) and (
        account.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")

    adjustments = await crud.balance_adjustment.get_by_account(
        db=db, account_id=account_id, skip=skip, limit=limit
    )

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

    adjustment = await crud.balance_adjustment.get(db=db, id=id)
    if not adjustment:
        raise HTTPException(status_code=404, detail="Balance adjustment not found")
    if not crud.user.is_superuser(current_user) and (
        adjustment.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")

    return adjustment


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

    if crud.user.is_superuser(current_user):
        adjustments = await crud.balance_adjustment.get_multi(
            db, skip=skip, limit=limit
        )
    else:
        adjustments = await crud.balance_adjustment.get_by_user(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    enrich_event(request, database={"operation": "list_all_adjustments", "results_count": len(adjustments)})
    return adjustments
