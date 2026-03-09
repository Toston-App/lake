from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.api.api_v1.endpoints.expenses import create_expenses_bulk, delete_expenses_bulk
from app.api.api_v1.endpoints.incomes import create_incomes_bulk, delete_incomes_bulk
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


async def process_bulk_deletion(
    db: AsyncSession,
    ids: list[int],
    delete_function: callable,
    current_user: models.User,
    entity_type: str,
    request: Request,
) -> list[int]:
    """Helper function to process bulk deletions"""
    if not ids:
        return []

    try:
        ids_str = ",".join(str(x) for x in ids)
        result = await delete_function(db=db, ids=ids_str, current_user=current_user, request=request)

        return result.deleted_ids
    except Exception:
        return []


@router.delete("", response_model=schemas.BulkDeletionsResponse)
async def bulk_delete(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    to_delete: schemas.BulkDelete = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Delete incomes and expenses in bulk by id."""
    if not to_delete:
        raise HTTPException(status_code=400, detail="No data provided for deletion")

    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "bulk_delete",
            "incomes_count": len(to_delete.incomes) if to_delete.incomes else 0,
            "expenses_count": len(to_delete.expenses) if to_delete.expenses else 0,
        },
    )

    with timed() as t:
        incomes_result = await process_bulk_deletion(
            db=db,
            ids=to_delete.incomes or [],
            delete_function=delete_incomes_bulk,
            current_user=current_user,
            entity_type="incomes",
            request=request,
        )

        expenses_result = await process_bulk_deletion(
            db=db,
            ids=to_delete.expenses or [],
            delete_function=delete_expenses_bulk,
            current_user=current_user,
            entity_type="expenses",
            request=request,
        )

    enrich_event(
        request,
        database={"operation": "bulk_delete", "duration_ms": t.ms},
        bulk={
            "incomes_deleted": len(incomes_result),
            "expenses_deleted": len(expenses_result),
            "total_deleted": len(incomes_result) + len(expenses_result),
        },
    )

    return {
        "incomes": incomes_result,
        "expenses": expenses_result,
    }


@router.post("", response_model=schemas.BulkCreationsResponse)
async def bulk_create(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    to_create: schemas.BulkCreate = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Create incomes and expenses in bulk by id."""
    if not to_create:
        raise HTTPException(status_code=400, detail="No data provided for deletion")

    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "bulk_create",
            "incomes_count": len(to_create.incomes) if to_create.incomes else 0,
            "expenses_count": len(to_create.expenses) if to_create.expenses else 0,
        },
    )

    with timed() as t:
        incomes_result = await create_incomes_bulk(
            db=db, incomes_in=to_create.incomes, current_user=current_user, request=request
        )

        expenses_result = await create_expenses_bulk(
            db=db, expenses_in=to_create.expenses, current_user=current_user, request=request
        )

    enrich_event(
        request,
        database={"operation": "bulk_create", "duration_ms": t.ms},
        bulk={
            "incomes_created": len(incomes_result),
            "expenses_created": len(expenses_result),
            "total_created": len(incomes_result) + len(expenses_result),
        },
    )

    return {
        "incomes": incomes_result,
        "expenses": expenses_result,
    }
