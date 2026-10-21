import calendar
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.api.deps import DateFilterType
from app.services import transactions as transaction_service
from app.services.errors import ServiceError, raise_http
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.get("/getAll", response_model=list[schemas.Income])
async def read_incomes(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve incomes.
    """
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
        },
        query={"type": "list_incomes", "skip": skip, "limit": limit},
    )

    with timed() as t:
        if crud.user.is_superuser(current_user):
            incomes = await crud.income.get_multi(db, skip=skip, limit=limit)
        else:
            incomes = await crud.income.get_multi_by_owner(
                db=db, owner_id=current_user.id, skip=skip, limit=limit
            )

    enrich_event(
        request,
        database={
            "operation": "list_incomes",
            "duration_ms": t.ms,
            "results_count": len(incomes),
        },
    )

    return incomes


@router.get("/{date_filter_type}/{date}", response_model=list[schemas.Income])
async def read_incomes_by_date(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    date_filter_type: DateFilterType = DateFilterType.date,
    date: str = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve incomes filtered by type.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={
            "type": "filter_incomes_by_date",
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
            incomes = await crud.income.get_multi_by_date(
                db=db, owner_id=current_user.id, start_date=start_date, end_date=end_date
            )

        enrich_event(
            request,
            database={
                "operation": "filter_incomes_by_date",
                "duration_ms": t.ms,
                "results_count": len(incomes),
            },
            date_range={
                "start": str(start_date),
                "end": str(end_date),
                "days": (end_date - start_date).days + 1,
            },
        )

        return incomes

    return []


@router.post("", response_model=schemas.Income)
async def create_income(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    income_in: schemas.IncomeCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new income.
    """
    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
        },
        operation={
            "type": "create_income",
            "source": income_in.made_from or "api",
        },
    )

    try:
        with timed() as t:
            income = await transaction_service.create_income(
                db, current_user, income_in
            )
    except ServiceError as exc:
        raise_http(exc)

    enrich_event(
        request,
        database={
            "operation": "create_income",
            "duration_ms": t.ms,
            "success": income is not None,
        },
        transaction={
            "type": "income",
            "id": income.id if income else None,
            "amount": float(income_in.amount),
            "date": str(income_in.date) if income_in.date else None,
            "has_subcategory": income_in.subcategory_id is not None,
            "has_place": income_in.place_id is not None,
            "has_account": income_in.account_id is not None,
        },
    )

    return income


@router.post("/bulk", response_model=list[schemas.Income])
async def create_incomes_bulk(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    incomes_in: list[schemas.IncomeCreate],
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create multiple incomes at once.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "bulk_create_incomes", "count": len(incomes_in)},
        bulk={
            "item_count": len(incomes_in),
            "total_amount": sum(float(i.amount) for i in incomes_in),
        },
    )

    try:
        with timed() as t:
            incomes = await transaction_service.create_incomes_bulk(
                db, current_user, incomes_in
            )
    except ServiceError as exc:
        raise_http(exc)

    enrich_event(
        request,
        database={
            "operation": "bulk_create_incomes",
            "duration_ms": t.ms,
            "success_count": len(incomes),
            "avg_duration_per_item_ms": round(t.ms / len(incomes_in), 2) if incomes_in else 0,
        },
    )

    return incomes


@router.get("/{id}", response_model=schemas.Income)
async def read_income(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get income by ID.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_income_by_id", "income_id": id},
    )

    try:
        return await transaction_service.get_income(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)


@router.put("/{id}", response_model=schemas.Income)
async def update_income(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    income_in: schemas.IncomeUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an income.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "update_income", "income_id": id},
    )

    original = await read_income(db=db, id=id, current_user=current_user, request=request)
    changes = {}
    if income_in.amount and income_in.amount != original.amount:
        changes["amount"] = {"from": float(original.amount), "to": float(income_in.amount)}
    if income_in.account_id and income_in.account_id != original.account_id:
        changes["account_id"] = {"from": original.account_id, "to": income_in.account_id}

    try:
        with timed() as t:
            updated_income = await transaction_service.update_income(
                db, current_user, id, income_in
            )
    except ServiceError as exc:
        raise_http(exc)

    enrich_event(
        request,
        database={
            "operation": "update_income",
            "duration_ms": t.ms,
            "success": updated_income is not None,
        },
        transaction={
            "id": id,
            "changes": changes,
            "fields_changed": len(changes),
        },
    )

    return updated_income


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_income(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an income.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "delete_income", "income_id": id},
    )

    try:
        with timed() as t:
            income = await transaction_service.delete_income(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)

    enrich_event(
        request,
        database={
            "operation": "delete_income",
            "duration_ms": t.ms,
            "success": True,
        },
        transaction={
            "id": id,
            "amount": float(income.amount),
            "had_account": income.account_id is not None,
            "had_subcategory": income.subcategory_id is not None,
        },
    )

    return schemas.DeletionResponse(message=f"Item {id} deleted")

# TODO: make a helper to delete and reutilize it in the bulk delete
@router.delete("/bulk/{ids}", response_model=schemas.BulkDeletionResponse)
async def delete_incomes_bulk(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    ids: str,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete multiple incomes at once.
    Format: /bulk/1,2,3
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "bulk_delete_incomes"},
    )

    try:
        id_list = [int(id.strip()) for id in ids.split(",")]
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid ID format. Use comma-separated integers"
        )

    enrich_event(
        request,
        bulk={
            "requested_count": len(id_list),
            "requested_ids": id_list[:10],
        },
    )

    try:
        with timed() as t:
            removed_incomes = await transaction_service.delete_incomes_bulk(
                db, current_user, id_list
            )
    except ServiceError as exc:
        raise_http(exc)
    total_amount_deleted = sum(float(income.amount) for income in removed_incomes)

    enrich_event(
        request,
        database={
            "operation": "bulk_delete_incomes",
            "duration_ms": t.ms,
            "success": True,
        },
        bulk={
            "deleted_count": len(removed_incomes),
            "total_amount_deleted": total_amount_deleted,
            "success_rate": round(len(removed_incomes) / len(id_list) * 100, 2) if id_list else 0,
        },
    )

    return schemas.BulkDeletionResponse(
        message=f"Deleted {len(removed_incomes)} incomes",
        deleted_ids=[i.id for i in removed_incomes],
    )
