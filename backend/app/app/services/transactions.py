"""Transaction operations shared by API routes and chat tools."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi_pagination.bases import AbstractParams
from sqlalchemy import update as updateDb
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.crud import crud_transaction
from app.schemas.transaction import AmountOperator, OrderDirection, TransactionType
from app.services.errors import DomainError, NotFoundError, PermissionDeniedError
from app.utilities.redis import invalidate_user_cache


def _is_superuser(user: models.User) -> bool:
    return crud.user.is_superuser(user)


async def get_expense(db: AsyncSession, user: models.User, expense_id: int):
    expense = await crud.expense.get(db=db, id=expense_id)
    if not expense:
        raise NotFoundError("Expense not found")
    if not _is_superuser(user) and expense.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return expense


async def get_income(db: AsyncSession, user: models.User, income_id: int):
    income = await crud.income.get(db=db, id=income_id)
    if not income:
        raise NotFoundError("Income not found")
    if not _is_superuser(user) and income.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return income


async def get_transfer(db: AsyncSession, user: models.User, transfer_id: int):
    transfer = await crud.transfer.get(db=db, id=transfer_id)
    if not transfer:
        raise NotFoundError("Transfer not found")
    if not _is_superuser(user) and transfer.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return transfer


def _parse_transaction_date(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return fallback
    return fallback


async def _adjust_total(db: AsyncSession, obj: Any, amount: float) -> None:
    await db.execute(
        updateDb(obj.__class__)
        .where(obj.__class__.id == obj.id)
        .values(total=obj.total + amount)
        .execution_options(synchronize_session="fetch")
    )
    await db.commit()


async def _adjust_expense_totals(
    db: AsyncSession,
    *,
    category_id: int | None,
    subcategory_id: int | None,
    amount: float,
) -> None:
    if category_id:
        category = await crud.category.get(db=db, id=category_id)
        if category:
            await _adjust_total(db, category, amount)
    if subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=subcategory_id)
        if subcategory:
            await _adjust_total(db, subcategory, amount)


async def _adjust_income_totals(
    db: AsyncSession,
    *,
    subcategory_id: int | None,
    amount: float,
) -> None:
    if not subcategory_id:
        return
    subcategory = await crud.subcategory.get(db=db, id=subcategory_id)
    if not subcategory:
        return
    await _adjust_total(db, subcategory, amount)
    if subcategory.category_id:
        category = await crud.category.get(db=db, id=subcategory.category_id)
        if category:
            await _adjust_total(db, category, amount)


async def _ensure_owned_optional(
    db: AsyncSession, user: models.User, crud_obj: Any, obj_id: int | None
) -> int | None:
    """Return obj_id when it is owned by user (or user is superuser); None otherwise.

    Helper for create/validate flows where optional foreign-key fields that are
    missing or owned by someone else are silently skipped (set to None).
    """
    if not obj_id:
        return None
    obj = await crud_obj.get(db=db, id=obj_id)
    if not obj or (not _is_superuser(user) and obj.owner_id != user.id):
        return None
    return obj_id


async def _require_owned(
    db: AsyncSession, user: models.User, crud_obj: Any, obj_id: int, label: str
) -> Any:
    """Fetch a required foreign-key entity, enforcing existence and ownership."""
    obj = await crud_obj.get(db=db, id=obj_id)
    if not obj:
        raise NotFoundError(f"{label} not found")
    if not _is_superuser(user) and obj.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return obj


async def create_expense(
    db: AsyncSession,
    user: models.User,
    expense_in: schemas.ExpenseCreate,
    *,
    recurring_id: int | None = None,
):
    expense_in.account_id = await _ensure_owned_optional(
        db, user, crud.account, expense_in.account_id
    )
    expense_in.category_id = await _ensure_owned_optional(
        db, user, crud.category, expense_in.category_id
    )
    expense_in.subcategory_id = await _ensure_owned_optional(
        db, user, crud.subcategory, expense_in.subcategory_id
    )
    expense_in.place_id = await _ensure_owned_optional(
        db, user, crud.place, expense_in.place_id
    )
    expense = await crud.expense.create_with_owner(
        db=db, obj_in=expense_in, owner_id=user.id, recurring_id=recurring_id
    )
    await invalidate_user_cache(user.id)
    return expense


async def create_expenses_bulk(
    db: AsyncSession, user: models.User, expenses_in: list[schemas.ExpenseCreate]
):
    expenses = await crud.expense.create_multi_with_owner(
        db=db, obj_list=expenses_in, owner_id=user.id
    )
    await invalidate_user_cache(user.id)
    return expenses


async def update_expense(
    db: AsyncSession,
    user: models.User,
    expense_id: int,
    expense_in: schemas.ExpenseUpdate,
):
    expense = await get_expense(db, user, expense_id)
    original_amount = expense.amount
    original_account_id = expense.account_id
    original_category_id = expense.category_id
    original_subcategory_id = expense.subcategory_id

    if expense_in.place_id:
        place = await crud.place.get(db=db, id=expense_in.place_id)
        if not place or (not _is_superuser(user) and place.owner_id != user.id):
            expense_in.place_id = expense.place_id
    if expense_in.category_id:
        category = await crud.category.get(db=db, id=expense_in.category_id)
        if not category or (not _is_superuser(user) and category.owner_id != user.id):
            expense_in.category_id = expense.category_id
    if expense_in.subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=expense_in.subcategory_id)
        if not subcategory or (not _is_superuser(user) and subcategory.owner_id != user.id):
            expense_in.subcategory_id = expense.subcategory_id
    if expense_in.date:
        expense_in.date = _parse_transaction_date(expense_in.date, expense.date)
    if expense_in.account_id:
        account = await crud.account.get(db=db, id=expense_in.account_id)
        if not account or (not _is_superuser(user) and account.owner_id != user.id):
            expense_in.account_id = expense.account_id

    expense_in.updated_at = datetime.now(timezone.utc)
    updated_expense = await crud.expense.update(
        db=db, db_obj=expense, obj_in=expense_in
    )

    if (
        updated_expense.amount != original_amount
        or updated_expense.category_id != original_category_id
        or updated_expense.subcategory_id != original_subcategory_id
    ):
        await _adjust_expense_totals(
            db,
            category_id=original_category_id,
            subcategory_id=original_subcategory_id,
            amount=-original_amount,
        )
        await _adjust_expense_totals(
            db,
            category_id=updated_expense.category_id,
            subcategory_id=updated_expense.subcategory_id,
            amount=updated_expense.amount,
        )

    if (
        updated_expense.amount != original_amount
        or updated_expense.account_id != original_account_id
    ):
        if original_account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=user.id,
                id=original_account_id,
                column="total_expenses",
                amount=-original_amount,
            )
        if updated_expense.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=user.id,
                id=updated_expense.account_id,
                column="total_expenses",
                amount=updated_expense.amount,
            )
        if updated_expense.amount != original_amount:
            await crud.user.update_balance(
                db=db,
                user_id=user.id,
                is_Expense=True,
                amount=updated_expense.amount - original_amount,
            )

    await invalidate_user_cache(user.id)
    return updated_expense


async def delete_expense(db: AsyncSession, user: models.User, expense_id: int):
    expense = await get_expense(db, user, expense_id)
    await _adjust_expense_totals(
        db,
        category_id=expense.category_id,
        subcategory_id=expense.subcategory_id,
        amount=-expense.amount,
    )
    removed = await crud.expense.remove(db=db, id=expense_id)
    await crud.user.update_balance(
        db=db, user_id=user.id, is_Expense=True, amount=-removed.amount
    )
    if removed.account_id:
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=user.id,
            id=removed.account_id,
            column="total_expenses",
            amount=-removed.amount,
        )
    await invalidate_user_cache(user.id)
    return removed


async def delete_expenses_bulk(
    db: AsyncSession, user: models.User, expense_ids: list[int]
):
    removed = []
    for expense_id in expense_ids:
        try:
            removed.append(await delete_expense(db, user, expense_id))
        except NotFoundError:
            continue
    if not removed:
        raise NotFoundError("No valid expenses found")
    return removed


async def create_income(
    db: AsyncSession,
    user: models.User,
    income_in: schemas.IncomeCreate,
    *,
    recurring_id: int | None = None,
):
    income_in.account_id = await _ensure_owned_optional(
        db, user, crud.account, income_in.account_id
    )
    income_in.subcategory_id = await _ensure_owned_optional(
        db, user, crud.subcategory, income_in.subcategory_id
    )
    income_in.place_id = await _ensure_owned_optional(
        db, user, crud.place, income_in.place_id
    )
    income = await crud.income.create_with_owner(
        db=db, obj_in=income_in, owner_id=user.id, recurring_id=recurring_id
    )
    await invalidate_user_cache(user.id)
    return income


async def create_incomes_bulk(
    db: AsyncSession, user: models.User, incomes_in: list[schemas.IncomeCreate]
):
    incomes = await crud.income.create_multi_with_owner(
        db=db, obj_list=incomes_in, owner_id=user.id
    )
    await invalidate_user_cache(user.id)
    return incomes


async def update_income(
    db: AsyncSession,
    user: models.User,
    income_id: int,
    income_in: schemas.IncomeUpdate,
):
    income = await get_income(db, user, income_id)
    original_amount = income.amount
    original_account_id = income.account_id
    original_subcategory_id = income.subcategory_id

    if income_in.place_id:
        place = await crud.place.get(db=db, id=income_in.place_id)
        if not place or (not _is_superuser(user) and place.owner_id != user.id):
            income_in.place_id = income.place_id
    if income_in.subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=income_in.subcategory_id)
        if not subcategory or (not _is_superuser(user) and subcategory.owner_id != user.id):
            income_in.subcategory_id = income.subcategory_id
    if income_in.date:
        income_in.date = _parse_transaction_date(income_in.date, income.date)
    if income_in.account_id:
        account = await crud.account.get(db=db, id=income_in.account_id)
        if not account or (not _is_superuser(user) and account.owner_id != user.id):
            income_in.account_id = income.account_id

    income_in.updated_at = datetime.now(timezone.utc)
    updated_income = await crud.income.update(db=db, db_obj=income, obj_in=income_in)

    if (
        updated_income.amount != original_amount
        or updated_income.subcategory_id != original_subcategory_id
    ):
        await _adjust_income_totals(
            db, subcategory_id=original_subcategory_id, amount=-original_amount
        )
        await _adjust_income_totals(
            db,
            subcategory_id=updated_income.subcategory_id,
            amount=updated_income.amount,
        )

    if (
        updated_income.amount != original_amount
        or updated_income.account_id != original_account_id
    ):
        if original_account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=user.id,
                id=original_account_id,
                column="total_incomes",
                amount=-original_amount,
            )
        if updated_income.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=user.id,
                id=updated_income.account_id,
                column="total_incomes",
                amount=updated_income.amount,
            )
        if updated_income.amount != original_amount:
            await crud.user.update_balance(
                db=db,
                user_id=user.id,
                is_Expense=False,
                amount=updated_income.amount - original_amount,
            )

    await invalidate_user_cache(user.id)
    return updated_income


async def delete_income(db: AsyncSession, user: models.User, income_id: int):
    await get_income(db, user, income_id)
    removed = await crud.income.remove(db=db, id=income_id)
    await crud.user.update_balance(
        db=db, user_id=user.id, is_Expense=False, amount=-removed.amount
    )
    if removed.account_id:
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=user.id,
            id=removed.account_id,
            column="total_incomes",
            amount=-removed.amount,
        )
    await _adjust_income_totals(
        db, subcategory_id=removed.subcategory_id, amount=-removed.amount
    )
    await invalidate_user_cache(user.id)
    return removed


async def delete_incomes_bulk(
    db: AsyncSession, user: models.User, income_ids: list[int]
):
    removed = []
    for income_id in income_ids:
        try:
            removed.append(await delete_income(db, user, income_id))
        except NotFoundError:
            continue
    if not removed:
        raise NotFoundError("No valid incomes found")
    return removed


async def create_transfer(
    db: AsyncSession,
    user: models.User,
    transfer_in: schemas.TransferCreate,
    *,
    recurring_id: int | None = None,
):
    await _require_owned(db, user, crud.account, transfer_in.from_acc, "Account")
    await _require_owned(db, user, crud.account, transfer_in.to_acc, "Account")
    transfer = await crud.transfer.create_with_owner(
        db=db, obj_in=transfer_in, owner_id=user.id, recurring_id=recurring_id
    )
    if transfer is None:
        raise DomainError("Account not found")
    await invalidate_user_cache(user.id)
    return transfer


async def update_transfer(
    db: AsyncSession,
    user: models.User,
    transfer_id: int,
    transfer_in: schemas.TransferUpdate,
):
    existing_transfer = await get_transfer(db, user, transfer_id)
    original_from_acc = existing_transfer.from_acc
    original_to_acc = existing_transfer.to_acc
    original_amount = existing_transfer.amount

    provided_fields = getattr(transfer_in, "model_fields_set", None) or getattr(
        transfer_in, "__fields_set__", set()
    )
    if "date" in provided_fields:
        transfer_in.date = _parse_transaction_date(transfer_in.date, existing_transfer.date)
    if transfer_in.from_acc is not None:
        from_acc = await crud.account.get(db=db, id=transfer_in.from_acc)
        if not from_acc or (not _is_superuser(user) and from_acc.owner_id != user.id):
            transfer_in.from_acc = existing_transfer.from_acc
    if transfer_in.to_acc is not None:
        to_acc = await crud.account.get(db=db, id=transfer_in.to_acc)
        if not to_acc or (not _is_superuser(user) and to_acc.owner_id != user.id):
            transfer_in.to_acc = existing_transfer.to_acc
    transfer_in.updated_at = datetime.now(timezone.utc)
    try:
        updated_transfer = await crud.transfer.update(
            db=db, db_obj=existing_transfer, obj_in=transfer_in
        )
    except Exception as exc:
        raise DomainError("Error updating transfer.") from exc

    if transfer_in.from_acc is not None and original_from_acc != transfer_in.from_acc:
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=user.id,
            id=original_from_acc,
            column="total_transfers_out",
            amount=-original_amount,
        )
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=user.id,
            id=transfer_in.from_acc,
            column="total_transfers_out",
            amount=updated_transfer.amount,
        )

    if transfer_in.to_acc is not None and original_to_acc != transfer_in.to_acc:
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=user.id,
            id=original_to_acc,
            column="total_transfers_in",
            amount=-original_amount,
        )
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=user.id,
            id=transfer_in.to_acc,
            column="total_transfers_in",
            amount=updated_transfer.amount,
        )

    if (
        transfer_in.amount is not None
        and original_amount != transfer_in.amount
        and (transfer_in.from_acc is None or original_from_acc == transfer_in.from_acc)
        and (transfer_in.to_acc is None or original_to_acc == transfer_in.to_acc)
    ):
        amount_difference = transfer_in.amount - original_amount
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=user.id,
            id=updated_transfer.from_acc,
            column="total_transfers_out",
            amount=amount_difference,
        )
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=user.id,
            id=updated_transfer.to_acc,
            column="total_transfers_in",
            amount=amount_difference,
        )

    await invalidate_user_cache(user.id)
    return updated_transfer


async def delete_transfer(db: AsyncSession, user: models.User, transfer_id: int):
    transfer = await get_transfer(db, user, transfer_id)
    removed = await crud.transfer.remove(db=db, id=transfer.id)
    await crud.account.update_by_id_and_field(
        db=db,
        owner_id=user.id,
        id=removed.from_acc,
        column="total_transfers_out",
        amount=-removed.amount,
    )
    await crud.account.update_by_id_and_field(
        db=db,
        owner_id=user.id,
        id=removed.to_acc,
        column="total_transfers_in",
        amount=-removed.amount,
    )
    await invalidate_user_cache(user.id)
    return removed


async def bulk_create_transactions(
    db: AsyncSession,
    user: models.User,
    bulk: schemas.BulkCreate,
    *,
    made_from: str | None = None,
):
    incomes_in = []
    expenses_in = []
    for income in bulk.incomes or []:
        data = income.model_dump() if hasattr(income, "model_dump") else income.dict()
        if made_from:
            data.setdefault("made_from", made_from)
        incomes_in.append(schemas.IncomeCreate(**data))
    for expense in bulk.expenses or []:
        data = expense.model_dump() if hasattr(expense, "model_dump") else expense.dict()
        if made_from:
            data.setdefault("made_from", made_from)
        expenses_in.append(schemas.ExpenseCreate(**data))

    return {
        "incomes": await create_incomes_bulk(db, user, incomes_in) if incomes_in else [],
        "expenses": await create_expenses_bulk(db, user, expenses_in)
        if expenses_in
        else [],
    }


async def bulk_delete_transactions(
    db: AsyncSession, user: models.User, bulk: schemas.BulkDelete
):
    incomes = []
    expenses = []
    if bulk.incomes:
        incomes = await delete_incomes_bulk(db, user, bulk.incomes)
    if bulk.expenses:
        expenses = await delete_expenses_bulk(db, user, bulk.expenses)
    return {"incomes": incomes, "expenses": expenses}


async def search_transactions(
    db: AsyncSession,
    user: models.User,
    *,
    order: OrderDirection = OrderDirection.desc,
    search: str | None = None,
    amount: float | None = None,
    amount_operator: AmountOperator | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    accounts: list[int] | None = None,
    categories: list[int] | None = None,
    places: list[int] | None = None,
    transaction_type: list[TransactionType] | None = None,
    params: AbstractParams | None = None,
):
    return await crud_transaction.get_multi_by_owner_with_filters(
        db=db,
        owner_id=user.id,
        order=order,
        search=search,
        amount=amount,
        amount_operator=amount_operator,
        start_date=start_date,
        end_date=end_date,
        accounts=accounts,
        categories=categories,
        places=places,
        transaction_type=transaction_type,
        params=params,
    )
