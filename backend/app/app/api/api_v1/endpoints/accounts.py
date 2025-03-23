from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.account import AccountType

router = APIRouter()


@router.get("", response_model=list[schemas.Account])
async def read_accounts(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve accounts.
    """
    if crud.user.is_superuser(current_user):
        accounts = await crud.account.get_multi(db, skip=skip, limit=limit)
    else:
        accounts = await crud.account.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    return accounts


@router.post("", response_model=schemas.Account)
async def create_account(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    account_in: schemas.AccountCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new account.
    """
    account = await crud.account.create_with_owner(
        db=db, obj_in=account_in, owner_id=current_user.id
    )
    return account


@router.get("/{id}", response_model=schemas.Account)
async def read_account(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get account by ID.
    """
    account = await crud.account.get(db=db, id=id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if not crud.user.is_superuser(current_user) and (
        account.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return account


@router.put("/{id}", response_model=schemas.Account)
async def update_account(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    # account_in: schemas.AccountUpdate,
    name: str = Body(None),
    initial_balance: float = Body(None),
    color: str = Body(None),
    type: AccountType = Body(None),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an account.
    """
    account = await read_account(db=db, id=id, current_user=current_user)

    current_account_data = jsonable_encoder(account)
    account_in = schemas.AccountUpdate(**current_account_data)

    if name is not None:
        account_in.name = name
    if initial_balance is not None:
        account_in.initial_balance = initial_balance
        account_in.current_balance += initial_balance - account.initial_balance
    if color is not None:
        account_in.color = color
    if type is not None:
        account_in.type = type

    account_in.updated_at = datetime.now(timezone.utc)
    account = await crud.account.update(db=db, db_obj=account, obj_in=account_in)
    return account


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_account(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an account.
    """
    account = await read_account(db=db, id=id, current_user=current_user)
    account = await crud.account.remove(db=db, id=id)
    return schemas.DeletionResponse(message=f"Account {id} deleted")
    return account
