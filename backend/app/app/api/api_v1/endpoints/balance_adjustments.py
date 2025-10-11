from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps

router = APIRouter()


@router.post("", response_model=schemas.BalanceAdjustment)
async def create_balance_adjustment(
    *,
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
    """
    # Get the account and verify ownership
    account = await crud.account.get(db=db, id=adjustment_in.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not crud.user.is_superuser(current_user) and (
        account.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")

    # Store the old balance
    old_balance = account.current_balance

    # Create the balance adjustment record
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

    return adjustment


@router.get("/account/{account_id}", response_model=list[schemas.BalanceAdjustment])
async def read_balance_adjustments_by_account(
    *,
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
    # Verify account exists and user has access
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

    return adjustments


@router.get("/{id}", response_model=schemas.BalanceAdjustment)
async def read_balance_adjustment(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a specific balance adjustment by ID.
    """
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
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve all balance adjustments made by the current user.
    Superusers can see all adjustments.
    """
    if crud.user.is_superuser(current_user):
        adjustments = await crud.balance_adjustment.get_multi(
            db, skip=skip, limit=limit
        )
    else:
        adjustments = await crud.balance_adjustment.get_by_user(
            db=db, user_id=current_user.id, skip=skip, limit=limit
        )

    return adjustments
