from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api.api_v1.endpoints.incomes import delete_incomes_bulk, create_incomes_bulk
from app.api.api_v1.endpoints.expenses import delete_expenses_bulk, create_expenses_bulk
from app.api import deps

router = APIRouter()

async def process_bulk_deletion(
    db: AsyncSession,
    ids: List[int],
    delete_function: callable,
    current_user: models.User,
    entity_type: str
) -> List[int]:
    """Helper function to process bulk deletions"""
    if not ids:
        return []

    try:
        ids_str = ','.join(str(x) for x in ids)
        result = await delete_function(
            db=db,
            ids=ids_str,
            current_user=current_user
        )

        return result.deleted_ids
    except Exception as e:
        return []

@router.delete("/", response_model=schemas.BulkDeletionsResponse)
async def bulk_delete(
    db: AsyncSession = Depends(deps.async_get_db),
    to_delete: schemas.BulkDelete = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Delete incomes and expenses in bulk by id."""
    if not to_delete:
        raise HTTPException(
            status_code=400,
            detail="No data provided for deletion"
        )

    incomes_result = await process_bulk_deletion(
        db=db,
        ids=to_delete.incomes or [],
        delete_function=delete_incomes_bulk,
        current_user=current_user,
        entity_type="incomes"
    )

    expenses_result = await process_bulk_deletion(
        db=db,
        ids=to_delete.expenses or [],
        delete_function=delete_expenses_bulk,
        current_user=current_user,
        entity_type="expenses"
    )

    return {
        "incomes": incomes_result,
        "expenses": expenses_result,
    }

@router.post("/", response_model=schemas.BulkCreationsResponse)
async def bulk_create(
    db: AsyncSession = Depends(deps.async_get_db),
    to_create: schemas.BulkCreate = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Create incomes and expenses in bulk by id."""
    if not to_create:
        raise HTTPException(
            status_code=400,
            detail="No data provided for deletion"
        )

    incomes_result = await create_incomes_bulk(
        db=db,
        incomes_in=to_create.incomes,
        current_user=current_user
    )

    expenses_result = await create_expenses_bulk(
        db=db,
        expenses_in=to_create.expenses,
        current_user=current_user
    )

    return {
        "incomes": incomes_result,
        "expenses": expenses_result,
    }
