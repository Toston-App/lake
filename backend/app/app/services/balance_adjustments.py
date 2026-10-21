"""Balance adjustment services shared by API and chat."""

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.services.errors import NotFoundError, PermissionDeniedError
from app.utilities.redis import invalidate_user_cache


async def create_balance_adjustment(
    db: AsyncSession,
    user: models.User,
    adjustment_in: schemas.BalanceAdjustmentCreate,
):
    account = await crud.account.get(db=db, id=adjustment_in.account_id)
    if not account:
        raise NotFoundError("Account not found")
    if not crud.user.is_superuser(user) and account.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")

    old_balance = account.current_balance
    adjustment = await crud.balance_adjustment.create_with_user(
        db=db,
        obj_in=adjustment_in,
        owner_id=user.id,
        old_balance=old_balance,
    )

    account_data = jsonable_encoder(account)
    account_update = schemas.AccountUpdate(**account_data)
    account_update.current_balance = adjustment_in.new_balance
    await crud.account.update(db=db, db_obj=account, obj_in=account_update)

    adjustment_amount = adjustment_in.new_balance - old_balance
    user_data = jsonable_encoder(user)
    user_update = schemas.UserUpdate(**user_data)
    user_update.balance_total = user.balance_total + adjustment_amount
    await crud.user.update(db=db, db_obj=user, obj_in=user_update)

    await invalidate_user_cache(user.id)
    return adjustment


async def list_balance_adjustments_by_account(
    db: AsyncSession,
    user: models.User,
    account_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
):
    account = await crud.account.get(db=db, id=account_id)
    if not account:
        raise NotFoundError("Account not found")
    if not crud.user.is_superuser(user) and account.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return await crud.balance_adjustment.get_by_account(
        db=db, account_id=account_id, skip=skip, limit=limit
    )


async def get_balance_adjustment(
    db: AsyncSession, user: models.User, adjustment_id: int
):
    adjustment = await crud.balance_adjustment.get(db=db, id=adjustment_id)
    if not adjustment:
        raise NotFoundError("Balance adjustment not found")
    if not crud.user.is_superuser(user) and adjustment.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return adjustment


async def list_balance_adjustments(
    db: AsyncSession,
    user: models.User,
    *,
    skip: int = 0,
    limit: int = 100,
):
    if crud.user.is_superuser(user):
        return await crud.balance_adjustment.get_multi(db, skip=skip, limit=limit)
    return await crud.balance_adjustment.get_by_user(
        db=db, owner_id=user.id, skip=skip, limit=limit
    )
