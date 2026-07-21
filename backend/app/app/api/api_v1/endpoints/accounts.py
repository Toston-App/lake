from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.models.account import AccountType
from app.services import accounts as account_service
from app.services.errors import ServiceError, raise_http
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
        accounts = await account_service.list_accounts(
            db, current_user, skip=skip, limit=limit
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
            "has_initial_balance": account_in.initial_balance is not None
            and account_in.initial_balance != 0,
        },
    )

    with timed() as t:
        account = await account_service.create_account(db, current_user, account_in)

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
            "initial_balance": float(account_in.initial_balance)
            if account_in.initial_balance
            else 0,
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

    try:
        return await account_service.get_account(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)


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
    logo: str = Body(None),
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

    changes = {}
    if name is not None:
        changes["name"] = True
    if initial_balance is not None:
        existing = await read_account(db=db, id=id, current_user=current_user, request=request)
        changes["initial_balance"] = {"from": float(existing.initial_balance), "to": initial_balance}
    if color is not None:
        changes["color"] = True
    if type is not None:
        existing = await read_account(db=db, id=id, current_user=current_user, request=request)
        changes["type"] = {"from": existing.type.value if existing.type else None, "to": type.value}
    if logo is not None:
        changes["logo"] = logo

    try:
        with timed() as t:
            account = await account_service.update_account(
                db,
                current_user,
                id,
                name=name,
                initial_balance=initial_balance,
                color=color,
                type=type,
                logo=logo,
            )
    except ServiceError as exc:
        raise_http(exc)

    changes["initial_balance"] = {
        "from": float(account.initial_balance),
        "to": initial_balance,
    }

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

    try:
        with timed() as t:
            await account_service.delete_account(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)

    enrich_event(
        request,
        database={"operation": "delete_account", "duration_ms": t.ms, "success": True},
    )

    return schemas.DeletionResponse(message=f"Account {id} deleted")
