import calendar
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update as updateDb
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.api.deps import DateFilterType

router = APIRouter()


@router.get("/getAll", response_model=list[schemas.Income])
async def read_incomes(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve incomes.
    """
    if crud.user.is_superuser(current_user):
        incomes = await crud.income.get_multi(db, skip=skip, limit=limit)
    else:
        incomes = await crud.income.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit
        )

    return incomes


@router.get("/{date_filter_type}/{date}", response_model=list[schemas.Income])
async def read_incomes(
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: str = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve incomes filtered by type.
    """
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
        incomes = await crud.income.get_multi_by_date(
            db=db, owner_id=current_user.id, start_date=start_date, end_date=end_date
        )
        return incomes

    return []


@router.post("", response_model=schemas.Income)
async def create_income(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    income_in: schemas.IncomeCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new income.
    """
    income = await crud.income.create_with_owner(
        db=db, obj_in=income_in, owner_id=current_user.id
    )
    return income


@router.post("/bulk", response_model=list[schemas.Income])
async def create_incomes_bulk(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    incomes_in: list[schemas.IncomeCreate],
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create multiple incomes at once.
    """
    incomes = await crud.income.create_multi_with_owner(
        db=db, obj_list=incomes_in, owner_id=current_user.id
    )
    return incomes


@router.get("/{id}", response_model=schemas.Income)
async def read_income(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get income by ID.
    """
    income = await crud.income.get(db=db, id=id)
    if not income:
        raise HTTPException(status_code=404, detail="Income not found")
    if not crud.user.is_superuser(current_user) and (
        income.owner_id != current_user.id
    ):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return income


@router.put("/{id}", response_model=schemas.Income)
async def update_income(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    income_in: schemas.IncomeUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an income.
    """
    income = await read_income(db=db, id=id, current_user=current_user)

    # TODO: Check there are changes
    # Store original values for later comparison
    original_amount = income.amount
    original_account_id = income.account_id
    original_subcategory_id = income.subcategory_id

    if income_in.place_id:
        place = await crud.place.get(db=db, id=income_in.place_id)
        if not place:
            income_in.place_id = income.place_id

    if income_in.subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=income_in.subcategory_id)
        if not subcategory:
            income_in.subcategory_id = income.subcategory_id

    if income_in.date:
        try:
            income_in.date = datetime.strptime(income_in.date, "%Y-%m-%d").date()
        except:
            income.date = income.date

    if income_in.account_id:
        account = await crud.account.get(db=db, id=income_in.account_id)
        if not account:
            income_in.account_id = income.account_id

    income_in.updated_at = datetime.now(timezone.utc)
    updated_income = await crud.income.update(db=db, db_obj=income, obj_in=income_in)

    # Update subcategory and category totals if amount or subcategory_id has changed
    if updated_income.amount != original_amount or updated_income.subcategory_id != original_subcategory_id:
        # Update original subcategory total and its category if it exists
        if original_subcategory_id:
            original_subcategory = await crud.subcategory.get(db=db, id=original_subcategory_id)
            if original_subcategory:
                # Update original subcategory total
                await db.execute(
                    updateDb(original_subcategory.__class__)
                    .where(original_subcategory.__class__.id == original_subcategory.id)
                    .values(total=original_subcategory.total - original_amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

                # Update original category total if it exists
                if original_subcategory.category_id:
                    original_category = await crud.category.get(db=db, id=original_subcategory.category_id)
                    if original_category:
                        await db.execute(
                            updateDb(original_category.__class__)
                            .where(original_category.__class__.id == original_category.id)
                            .execution_options(synchronize_session="fetch")
                            .values(total=original_category.total - original_amount)
                        )
                        await db.commit()

        # Update new subcategory total and its category if it exists
        if updated_income.subcategory_id:
            new_subcategory = await crud.subcategory.get(db=db, id=updated_income.subcategory_id)
            if new_subcategory:
                # Update new subcategory total
                await db.execute(
                    updateDb(new_subcategory.__class__)
                    .where(new_subcategory.__class__.id == new_subcategory.id)
                    .values(total=new_subcategory.total + updated_income.amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

                # Update new category total if it exists
                if new_subcategory.category_id:
                    new_category = await crud.category.get(db=db, id=new_subcategory.category_id)
                    if new_category:
                        await db.execute(
                            updateDb(new_category.__class__)
                            .where(new_category.__class__.id == new_category.id)
                            .values(total=new_category.total + updated_income.amount)
                            .execution_options(synchronize_session="fetch")
                        )
                        await db.commit()

    if (
        updated_income.amount != original_amount
        or updated_income.account_id != original_account_id
    ):
        # Update original account
        if original_account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=current_user.id,
                id=original_account_id,
                column="total_incomes",
                amount=-original_amount,
            )

        # Update new account
        if updated_income.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=current_user.id,
                id=updated_income.account_id,
                column="total_incomes",
                amount=updated_income.amount,
            )

        # Update user's global balance
        if updated_income.amount != original_amount:
            amount_difference = updated_income.amount - original_amount
            await crud.user.update_balance(
                db=db,
                user_id=current_user.id,
                is_Expense=False,
                amount=amount_difference,
            )

    return updated_income


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_income(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an income.
    """
    income = await read_income(db=db, id=id, current_user=current_user)
    income = await crud.income.remove(db=db, id=id)

    # Remove the income from the user's balance
    await crud.user.update_balance(
        db=db, user_id=current_user.id, is_Expense=False, amount=-income.amount
    )

    if income.account_id:
        # amount is negative because it's an income, and we want to subtract instead of add
        await crud.account.update_by_id_and_field(
            db=db,
            owner_id=current_user.id,
            id=income.account_id,
            column="total_incomes",
            amount=-income.amount
        )

    # Update subcategory total and its category if they exist
    if income.subcategory_id:
        subcategory = await crud.subcategory.get(db=db, id=income.subcategory_id)

        if subcategory:
            # Update subcategory total
            await db.execute(
                updateDb(subcategory.__class__)
                .where(subcategory.__class__.id == subcategory.id)
                .values(total=subcategory.total - income.amount)
                .execution_options(synchronize_session="fetch")
            )
            await db.commit()

            # Update category total if it exists
            if subcategory.category_id:
                category = await crud.category.get(db=db, id=subcategory.category_id)

                if category:
                    await db.execute(
                        updateDb(category.__class__)
                        .where(category.__class__.id == category.id)
                        .values(total=category.total - income.amount)
                        .execution_options(synchronize_session="fetch")
                    )
                    await db.commit()


    return schemas.DeletionResponse(message=f"Item {id} deleted")

# TODO: make a helper to delete and reutilize it in the bulk delete
@router.delete("/bulk/{ids}", response_model=schemas.BulkDeletionResponse)
async def delete_incomes_bulk(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    ids: str,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete multiple incomes at once.
    Format: /bulk/1,2,3
    """
    try:
        id_list = [int(id.strip()) for id in ids.split(",")]
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid ID format. Use comma-separated integers"
        )

    # Collect valid incomes first
    valid_incomes = []
    for id in id_list:
        income = await crud.income.get(db=db, id=id)
        if not income:
            continue
        if not crud.user.is_superuser(current_user) and (
            income.owner_id != current_user.id
        ):
            raise HTTPException(
                status_code=400, detail=f"Not enough permissions for income {id}"
            )
        valid_incomes.append(income)

    if not valid_incomes:
        raise HTTPException(status_code=404, detail="No valid incomes found")

    # Update subcategory and category totals before deleting
    for income in valid_incomes:
        if income.subcategory_id:
            subcategory = await crud.subcategory.get(db=db, id=income.subcategory_id)
            if subcategory:
                # Update subcategory total
                await db.execute(
                    updateDb(subcategory.__class__)
                    .where(subcategory.__class__.id == subcategory.id)
                    .value(total=subcategory.total - income.amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

                # Update category total if it exists
                if subcategory.category_id:
                    category = await crud.category.get(db=db, id=subcategory.category_id)
                    if category:
                        await db.execute(
                            updateDb(category.__class__)
                            .where(category.__class__.id == category.id)
                            .value(total=category.total - income.amount)
                            .execution_options(synchronize_session="fetch")
                        )
                        await db.commit()

    # Only attempt to remove existing incomes
    valid_ids = [income.id for income in valid_incomes]
    removed_incomes = await crud.income.remove_multi(db=db, ids=valid_ids)

    # Update balances for successfully removed incomes
    for income in removed_incomes:
        await crud.user.update_balance(
            db=db, user_id=current_user.id, is_Expense=False, amount=-income.amount
        )
        if income.account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=current_user.id,
                id=income.account_id,
                column="total_incomes",
                amount=-income.amount,
            )

    return schemas.BulkDeletionResponse(
        message=f"Deleted {len(removed_incomes)} incomes",
        deleted_ids=[i.id for i in removed_incomes],
    )
