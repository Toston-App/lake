"""Account operations shared by API routes and chat tools."""

from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.models.account import AccountType
from app.services.errors import NotFoundError, PermissionDeniedError
from app.utilities.redis import invalidate_user_cache


async def list_accounts(
    db: AsyncSession,
    user: models.User,
    *,
    skip: int = 0,
    limit: int = 100,
):
    if crud.user.is_superuser(user):
        return await crud.account.get_multi(db, skip=skip, limit=limit)
    return await crud.account.get_multi_by_owner(
        db=db, owner_id=user.id, skip=skip, limit=limit
    )


async def get_account(db: AsyncSession, user: models.User, account_id: int):
    account = await crud.account.get(db=db, id=account_id)
    if not account:
        raise NotFoundError("Account not found")
    if not crud.user.is_superuser(user) and account.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return account


async def create_account(
    db: AsyncSession, user: models.User, account_in: schemas.AccountCreate
):
    account = await crud.account.create_with_owner(
        db=db, obj_in=account_in, owner_id=user.id
    )
    await invalidate_user_cache(user.id)
    return account


async def update_account(
    db: AsyncSession,
    user: models.User,
    account_id: int,
    *,
    name: str | None = None,
    initial_balance: float | None = None,
    color: str | None = None,
    type: AccountType | None = None,
    logo: str | None = None,
    account_in: schemas.AccountUpdate | None = None,
):
    account = await get_account(db, user, account_id)
    current_account_data = jsonable_encoder(account)
    update_in = schemas.AccountUpdate(**current_account_data)
    if account_in is not None:
        provided_fields = getattr(account_in, "model_fields_set", None) or getattr(
            account_in, "__fields_set__", set()
        )
        if name is None and "name" in provided_fields:
            name = account_in.name
        if initial_balance is None and "initial_balance" in provided_fields:
            initial_balance = account_in.initial_balance
        if color is None and "color" in provided_fields:
            color = account_in.color
        if type is None and "type" in provided_fields:
            type = account_in.type
        if logo is None and "logo" in provided_fields:
            logo = account_in.logo

    if name is not None:
        update_in.name = name
    if initial_balance is not None:
        balance_difference = initial_balance - account.initial_balance
        update_in.initial_balance = initial_balance
        update_in.current_balance = account.current_balance + balance_difference

        current_user_data = jsonable_encoder(user)
        user_in = schemas.UserUpdate(**current_user_data)
        user_in.balance_total = user.balance_total + balance_difference
        await crud.user.update(db=db, db_obj=user, obj_in=user_in)
    if color is not None:
        update_in.color = color
    if type is not None:
        update_in.type = type
    if logo is not None:
        update_in.logo = logo

    update_in.updated_at = datetime.now(timezone.utc)
    account = await crud.account.update(db=db, db_obj=account, obj_in=update_in)
    await invalidate_user_cache(user.id)
    return account


async def delete_account(db: AsyncSession, user: models.User, account_id: int):
    await get_account(db, user, account_id)
    if user.default_account_id == account_id:
        await crud.user.clear_default_account(db=db, user_id=user.id)
    await crud.account.remove(db=db, id=account_id)
    await invalidate_user_cache(user.id)
