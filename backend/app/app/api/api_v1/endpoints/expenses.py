import calendar
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update as updateDb
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.get("/getAll", response_model=list[schemas.Expense])
async def read_expenses(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve expenses.
    """
    # Add user context
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
        },
        query={
            "type": "list_expenses",
            "skip": skip,
            "limit": limit,
        },
    )
    
    with timed() as t:
        if crud.user.is_superuser(current_user):
            expenses = await crud.expense.get_multi(db, skip=skip, limit=limit)
        else:
            expenses = await crud.expense.get_multi_by_owner(
                db=db, owner_id=current_user.id, skip=skip, limit=limit
            )

    enrich_event(
        request,
        database={
            "operation": "list_expenses",
            "duration_ms": t.ms,
            "results_count": len(expenses),
        },
    )

    return expenses


class DateFilterType(str, Enum):
    date = "date"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"
    range = "range"


@router.get("/{date_filter_type}/{date}", response_model=list[schemas.Expense])
async def read_expenses_by_date(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: str = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve expenses filtered by type.
    """
    # Add user and filter context
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
        },
        query={
            "type": "filter_expenses_by_date",
            "date_filter_type": date_filter_type.value,
            "date_param": date,
        },
    )
    
    start_date: Date | None = None
    end_date: Date | None = None

    if date_filter_type == DateFilterType.date:
        try:
            start_date = datetime.strptime(date, "%Y-%m-%d").date()
            end_date = start_date
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

    elif date_filter_type == DateFilterType.week:
        try:
            start_date = datetime.strptime(date, "%Y-%m-%d").date()
            end_date = start_date + timedelta(days=7)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

    elif date_filter_type == DateFilterType.month:
        try:
            start_date = datetime.strptime(date, "%Y-%m").date()
            _, num_days = calendar.monthrange(start_date.year, start_date.month)
            end_date = start_date + timedelta(days=num_days - 1)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY-MM"
            )

    elif date_filter_type == DateFilterType.quarter:
        try:
            year_str, quarter_str = date.split("-")
            quarterNum = int(quarter_str.replace("Q", ""))
            year = int(year_str)

            if quarterNum < 1 or quarterNum > 4:
                raise ValueError("Quarter must be between 1 and 4")

            start_month = (quarterNum - 1) * 3 + 1
            end_month = quarterNum * 3
            start_date = Date(year, start_month, 1)
            _, end_day = calendar.monthrange(year, end_month)
            end_date = Date(year, end_month, end_day)

        except (ValueError, IndexError):
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY-QX"
            )

    elif date_filter_type == DateFilterType.year:
        try:
            year = int(date)
            start_date = Date(year, 1, 1)
            end_date = Date(year, 12, 31)
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY"
            )

    elif date_filter_type == DateFilterType.range:
        try:
            start_date_str, end_date_str = date.split(":")
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Date range must be in the format YYYY-MM-DD:YYYY-MM-DD",
            )

        if start_date > end_date:
            raise HTTPException(
                status_code=400, detail="Start date must be before end date"
            )

    if start_date and end_date:
        with timed() as t:
            expenses = await crud.expense.get_multi_by_date(
                db=db, owner_id=current_user.id, start_date=start_date, end_date=end_date
            )

        enrich_event(
            request,
            database={
                "operation": "filter_expenses_by_date",
                "duration_ms": t.ms,
                "results_count": len(expenses),
            },
            date_range={
                "start": str(start_date),
                "end": str(end_date),
                "days": (end_date - start_date).days + 1,
            },
        )

        return expenses

    return []


@router.post("", response_model=schemas.Expense)
async def create_expense(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    expense_in: schemas.ExpenseCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new expense.
    """
    # Add user context
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
        },
        operation={
            "type": "create_expense",
            "source": expense_in.made_from or "api",
        },
    )

    with timed() as t:
        expense = await crud.expense.create_with_owner(
            db=db, obj_in=expense_in, owner_id=current_user.id
        )

    enrich_event(
        request,
        database={
            "operation": "create_expense",
            "duration_ms": t.ms,
            "success": expense is not None,
        },
        transaction={
            "type": "expense",
            "id": expense.id if expense else None,
            "amount": float(expense_in.amount),
            "date": str(expense_in.date) if expense_in.date else None,
            "has_category": expense_in.category_id is not None,
            "has_subcategory": expense_in.subcategory_id is not None,
            "has_place": expense_in.place_id is not None,
            "has_account": expense_in.account_id is not None,
        },
    )

    return expense


@router.post("/bulk", response_model=list[schemas.Expense])
async def create_expenses_bulk(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    expenses_in: list[schemas.ExpenseCreate],
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create multiple expenses at once.
    """
    # Add user and bulk operation context
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
        },
        operation={
            "type": "bulk_create_expenses",
            "count": len(expenses_in),
        },
        bulk={
            "item_count": len(expenses_in),
            "total_amount": sum(float(e.amount) for e in expenses_in),
        },
    )

    with timed() as t:
        expenses = await crud.expense.create_multi_with_owner(
            db=db, obj_list=expenses_in, owner_id=current_user.id
        )

    enrich_event(
        request,
        database={
            "operation": "bulk_create_expenses",
            "duration_ms": t.ms,
            "success_count": len(expenses),
            "avg_duration_per_item_ms": round(t.ms / len(expenses_in), 2) if expenses_in else 0,
        },
    )

    return expenses


@router.get("/{id}", response_model=schemas.Expense)
async def read_expense(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get expense by ID.
    """
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
        },
        query={
            "type": "get_expense_by_id",
            "expense_id": id,
        },
    )

    expense = await crud.expense.get(db=db, id=id)
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if not crud.user.is_superuser(current_user) and (
        expense.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return expense


@router.put("/{id}", response_model=schemas.Expense)
async def update_expense(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    expense_in: schemas.ExpenseUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an expense.
    """
    # Add user context
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
        },
        operation={
            "type": "update_expense",
            "expense_id": id,
        },
    )

    expense = await read_expense(db=db, id=id, current_user=current_user, request=request)

    # TODO: Check there are changes
    # Store original values for later comparison
    original_amount = expense.amount
    original_account_id = expense.account_id
    original_category_id = expense.category_id
    original_subcategory_id = expense.subcategory_id

    if expense_in.place_id:
        place = await crud.place.get(db=db, id=expense_in.place_id)
        if not place:
            expense_in.place_id = expense.place_id

    if expense_in.category_id:
        category = await crud.category.get(db=db, id=expense_in.category_id)
        if not category:
            expense_in.category_id = expense.category_id

    if expense_in.subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=expense_in.subcategory_id)
        if not subcategory:
            expense_in.subcategory_id = expense.subcategory_id

    if expense_in.date:
        try:
            expense_in.date = datetime.strptime(expense_in.date, "%Y-%m-%d").date()
        except:
            expense_in.date = expense.date

    if expense_in.account_id:
        account = await crud.account.get(db=db, id=expense_in.account_id)
        if not account:
            expense_in.account_id = expense.account_id

    expense_in.updated_at = datetime.now(timezone.utc)

    # Track what changed
    changes = {}
    if expense_in.amount and expense_in.amount != original_amount:
        changes["amount"] = {"from": float(original_amount), "to": float(expense_in.amount)}
    if expense_in.account_id and expense_in.account_id != original_account_id:
        changes["account_id"] = {"from": original_account_id, "to": expense_in.account_id}
    if expense_in.category_id and expense_in.category_id != original_category_id:
        changes["category_id"] = {"from": original_category_id, "to": expense_in.category_id}

    with timed() as t:
        updated_expense = await crud.expense.update(
            db=db, db_obj=expense, obj_in=expense_in
        )

    enrich_event(
        request,
        database={
            "operation": "update_expense",
            "duration_ms": t.ms,
            "success": updated_expense is not None,
        },
        transaction={
            "id": id,
            "changes": changes,
            "fields_changed": len(changes),
        },
    )

    # Update category and subcategory totals if amount, category_id or subcategory_id has changed
    if updated_expense.amount != original_amount or updated_expense.category_id != original_category_id or updated_expense.subcategory_id != original_subcategory_id:
        # Update original category total if it exists
        if original_category_id:
            original_category = await crud.category.get(db=db, id=original_category_id)

            if original_category:
                await db.execute(
                    updateDb(original_category.__class__)
                    .where(original_category.__class__.id == original_category.id)
                    .values(total=original_category.total - original_amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

        # Update new category total if it exists
        if updated_expense.category_id:
            new_category = await crud.category.get(db=db, id=updated_expense.category_id)

            if new_category:
                await db.execute(
                    updateDb(new_category.__class__)
                    .where(new_category.__class__.id == new_category.id)
                    .values(total=new_category.total + updated_expense.amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

        # Update original subcategory total if it exists
        if original_subcategory_id:
            original_subcategory = await crud.subcategory.get(db=db, id=original_subcategory_id)

            if original_subcategory:
                await db.execute(
                    updateDb(original_subcategory.__class__)
                    .where(original_subcategory.__class__.id == original_subcategory.id)
                    .values(total=original_subcategory.total - original_amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

        # Update new subcategory total if it exists
        if updated_expense.subcategory_id:
            new_subcategory = await crud.subcategory.get(db=db, id=updated_expense.subcategory_id)

            if new_subcategory:
                await db.execute(
                    updateDb(new_subcategory.__class__)
                    .where(new_subcategory.__class__.id == new_subcategory.id)
                    .values(total=new_subcategory.total + updated_expense.amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

    if (
        updated_expense.amount != original_amount
        or updated_expense.account_id != original_account_id
    ):
        # Update original account
        if original_account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=current_user.id,
                id=original_account_id,
                column="total_expenses",
                amount=-original_amount,
            )

        # Update new account
        if updated_expense.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=current_user.id,
                id=updated_expense.account_id,
                column="total_expenses",
                amount=updated_expense.amount,
            )

        # Update user's global balance
        if updated_expense.amount != original_amount:
            amount_difference = updated_expense.amount - original_amount
            await crud.user.update_balance(
                db=db,
                user_id=current_user.id,
                is_Expense=True,
                amount=amount_difference,
            )

    return updated_expense


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_expense(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an expense.
    """
    # Add user context
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
        },
        operation={
            "type": "delete_expense",
            "expense_id": id,
        },
    )

    expense = await read_expense(db=db, id=id, current_user=current_user, request=request)

    # TODO: move this to crud and reutilize it in bulk deletion
    # Update category total if it exists
    if expense.category_id:
        category = await crud.category.get(db=db, id=expense.category_id)

        if category:
            await db.execute(
                updateDb(category.__class__)
                .where(category.__class__.id == category.id)
                .values(total=category.total - expense.amount)
                .execution_options(synchronize_session="fetch")
            )
            await db.commit()

    # Update subcategory total if it exists
    if expense.subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=expense.subcategory_id)

        if subcategory:
            await db.execute(
                updateDb(subcategory.__class__)
                .where(subcategory.__class__.id == subcategory.id)
                .values(total=subcategory.total - expense.amount)
                .execution_options(synchronize_session="fetch")
            )
            await db.commit()

    with timed() as t:
        expense = await crud.expense.remove(db=db, id=id)

        await crud.user.update_balance(
            db=db, user_id=current_user.id, is_Expense=True, amount=-expense.amount
        )

        if expense.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=current_user.id,
                id=expense.account_id,
                column="total_expenses",
                amount=-expense.amount,
            )

    enrich_event(
        request,
        database={
            "operation": "delete_expense",
            "duration_ms": t.ms,
            "success": True,
        },
        transaction={
            "id": id,
            "amount": float(expense.amount),
            "had_account": expense.account_id is not None,
            "had_category": expense.category_id is not None,
        },
    )

    return schemas.DeletionResponse(message=f"Item {id} deleted")


@router.delete("/bulk/{ids}", response_model=schemas.BulkDeletionResponse)
async def delete_expenses_bulk(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    ids: str,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete multiple expenses at once.
    Format: /bulk/1,2,3
    """
    # Add user context
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
        },
        operation={
            "type": "bulk_delete_expenses",
        },
    )

    try:
        id_list = [int(id.strip()) for id in ids.split(",")]
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid ID format. Use comma-separated integers"
        )

    # Add bulk operation context
    enrich_event(
        request,
        bulk={
            "requested_count": len(id_list),
            "requested_ids": id_list[:10],  # Sample first 10 IDs
        },
    )

    # Verify permissions for all expenses
    valid_ids = []
    expenses_to_delete = []
    for id in id_list:
        expense = await crud.expense.get(db=db, id=id)
        if not expense:
            continue
        if not crud.user.is_superuser(current_user) and (
            expense.owner_id != current_user.id
        ):
            raise HTTPException(
                status_code=400, detail=f"Not enough permissions for expense {id}"
            )
        valid_ids.append(expense.id)
        expenses_to_delete.append(expense)

    if not valid_ids:
        raise HTTPException(status_code=404, detail="No valid expenses found")

    # Update category and subcategory totals before deleting
    for expense in expenses_to_delete:
        # Update category total if it exists
        if expense.category_id:
            category = await crud.category.get(db=db, id=expense.category_id)

            if category:
                await db.execute(
                    updateDb(category.__class__)
                    .where(category.__class__.id == category.id)
                    .values(total=category.total - expense.amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

        # Update subcategory total if it exists
        if expense.subcategory_id:
            subcategory = await crud.subcategory.get(db=db, id=expense.subcategory_id)

            if subcategory:
                await db.execute(
                    updateDb(subcategory.__class__)
                    .where(subcategory.__class__.id == subcategory.id)
                    .values(total=subcategory.total - expense.amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

    with timed() as t:
        removed_expenses = await crud.expense.remove_multi(db=db, ids=valid_ids)

        total_amount_deleted = 0.0
        for expense in removed_expenses:
            total_amount_deleted += float(expense.amount)
            await crud.user.update_balance(
                db=db, user_id=current_user.id, is_Expense=True, amount=-expense.amount
            )
            if expense.account_id:
                await crud.account.update_by_id_and_field(
                    db=db,
                    owner_id=current_user.id,
                    id=expense.account_id,
                    column="total_expenses",
                    amount=-expense.amount,
                )

    enrich_event(
        request,
        database={
            "operation": "bulk_delete_expenses",
            "duration_ms": t.ms,
            "success": True,
        },
        bulk={
            "deleted_count": len(removed_expenses),
            "total_amount_deleted": total_amount_deleted,
            "success_rate": round(len(removed_expenses) / len(id_list) * 100, 2) if id_list else 0,
        },
    )

    return schemas.BulkDeletionResponse(
        message=f"Deleted {len(removed_expenses)} expenses",
        deleted_ids=[e.id for e in removed_expenses],
    )
