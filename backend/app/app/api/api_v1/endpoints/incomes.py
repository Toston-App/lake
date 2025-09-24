from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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
    start_date, end_date = deps.parse_date_filter(date_filter_type, date)

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
    updated_income = await crud.income.update_with_owner(db=db, db_obj=income, obj_in=income_in, owner_id=current_user.id)

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
    income = await crud.income.remove_with_owner(db=db, income_id=id, owner_id=current_user.id)
    if not income:
        raise HTTPException(status_code=404, detail="Income not found")


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

    # Use the owner-aware remove method for each income
    removed_incomes = []
    for income_id in id_list:
        income = await crud.income.remove_with_owner(db=db, income_id=income_id, owner_id=current_user.id)
        if income:
            removed_incomes.append(income)

    if not removed_incomes:
        raise HTTPException(status_code=404, detail="No valid incomes found")

    return schemas.BulkDeletionResponse(
        message=f"Deleted {len(removed_incomes)} incomes",
        deleted_ids=[i.id for i in removed_incomes],
    )
