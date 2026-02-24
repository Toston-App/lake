from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.account import AccountType
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.get("", response_model=list[schemas.Account])
async def read_accounts(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve accounts.
    """
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
        },
        query={"type": "list_accounts", "skip": skip, "limit": limit},
    )

    with timed() as t:
        if crud.user.is_superuser(current_user):
            accounts = await crud.account.get_multi(db, skip=skip, limit=limit)
        else:
            accounts = await crud.account.get_multi_by_owner(
                db=db, owner_id=current_user.id, skip=skip, limit=limit
            )

    enrich_event(
        request,
        database={
            "operation": "list_accounts",
            "duration_ms": t.ms,
            "results_count": len(accounts),
        },
    )

    return accounts


@router.post("", response_model=schemas.Account)
async def create_account(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    account_in: schemas.AccountCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new account.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "create_account",
            "account_type": account_in.type.value if account_in.type else None,
            "has_initial_balance": account_in.initial_balance is not None and account_in.initial_balance != 0,
        },
    )

    with timed() as t:
        account = await crud.account.create_with_owner(
            db=db, obj_in=account_in, owner_id=current_user.id
        )

    enrich_event(
        request,
        database={
            "operation": "create_account",
            "duration_ms": t.ms,
            "success": account is not None,
        },
        transaction={
            "type": "account",
            "id": account.id if account else None,
            "initial_balance": float(account_in.initial_balance) if account_in.initial_balance else 0,
        },
    )

    return account


@router.get("/{id}", response_model=schemas.Account)
async def read_account(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get account by ID.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_account_by_id", "account_id": id},
    )

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
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    name: str = Body(None),
    initial_balance: float = Body(None),
    color: str = Body(None),
    type: AccountType = Body(None),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an account.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "update_account", "account_id": id},
    )

    account = await read_account(db=db, id=id, current_user=current_user, request=request)

    current_account_data = jsonable_encoder(account)
    account_in = schemas.AccountUpdate(**current_account_data)

    changes = {}
    if name is not None:
        account_in.name = name
        changes["name"] = True
    if initial_balance is not None:
        balance_difference = initial_balance - account.initial_balance
        account_in.initial_balance = initial_balance
        account_in.current_balance += balance_difference

        current_user_data = jsonable_encoder(current_user)
        user_in = schemas.UserUpdate(**current_user_data)
        user_in.balance_total = current_user.balance_total + balance_difference
        await crud.user.update(db=db, db_obj=current_user, obj_in=user_in)

        changes["initial_balance"] = {"from": float(account.initial_balance), "to": initial_balance}
    if color is not None:
        account_in.color = color
        changes["color"] = True
    if type is not None:
        account_in.type = type
        changes["type"] = {"from": account.type.value if account.type else None, "to": type.value}

    account_in.updated_at = datetime.now(timezone.utc)

    with timed() as t:
        account = await crud.account.update(db=db, db_obj=account, obj_in=account_in)

    enrich_event(
        request,
        database={
            "operation": "update_account",
            "duration_ms": t.ms,
            "success": True,
        },
        transaction={
            "id": id,
            "changes": changes,
            "fields_changed": len(changes),
        },
    )

    return account


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_account(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an account.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "delete_account",
            "account_id": id,
            "was_default": current_user.default_account_id == id,
        },
    )

    await read_account(db=db, id=id, current_user=current_user, request=request)

    if current_user.default_account_id == id:
        await crud.user.clear_default_account(db=db, user_id=current_user.id)

    with timed() as t:
        await crud.account.remove(db=db, id=id)

    enrich_event(
        request,
        database={"operation": "delete_account", "duration_ms": t.ms, "success": True},
    )

    return schemas.DeletionResponse(message=f"Account {id} deleted")
