from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.services import recurring as recurring_service
from app.services.errors import ServiceError, raise_http
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


def _enrich(request: Request, user: models.User, operation: dict) -> None:
    enrich_event(
        request,
        user={"id": user.id, "email": user.email},
        operation=operation,
    )


@router.get("", response_model=list[schemas.RecurringTransactionDetail])
async def read_recurring_transactions(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    List the user's recurring transactions with their upcoming dates.
    """
    _enrich(request, current_user, {"type": "list_recurring"})
    with timed() as t:
        templates = await recurring_service.list_recurring(db, current_user)
    enrich_event(
        request,
        database={
            "operation": "list_recurring",
            "duration_ms": t.ms,
            "results_count": len(templates),
        },
    )
    return [recurring_service.to_detail(template) for template in templates]


@router.post("", response_model=schemas.RecurringTransactionDetail)
async def create_recurring_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    recurring_in: schemas.RecurringTransactionCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create a recurring transaction. Occurrences already due are created right away.
    """
    _enrich(
        request,
        current_user,
        {
            "type": "create_recurring",
            "transaction_type": recurring_in.type.value,
            "frequency": recurring_in.frequency.value,
        },
    )
    try:
        template = await recurring_service.create_recurring(
            db, current_user, recurring_in
        )
    except ServiceError as exc:
        raise_http(exc)
    return recurring_service.to_detail(template)


@router.post("/preview", response_model=schemas.RecurringPreview)
async def preview_recurring_transaction(
    *,
    request: Request,
    preview_in: schemas.RecurringPreviewRequest,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Show the next dates a schedule would produce, without saving it.
    """
    _enrich(request, current_user, {"type": "preview_recurring"})
    try:
        return recurring_service.preview(preview_in)
    except ServiceError as exc:
        raise_http(exc)


@router.post("/from-transaction", response_model=schemas.RecurringTransactionDetail)
async def create_recurring_from_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    recurring_in: schemas.RecurringFromTransaction,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Mark an existing transaction as recurring; it becomes the first occurrence.
    """
    _enrich(
        request,
        current_user,
        {
            "type": "create_recurring_from_transaction",
            "transaction_type": recurring_in.transaction_type.value,
            "transaction_id": recurring_in.transaction_id,
        },
    )
    try:
        template = await recurring_service.create_from_transaction(
            db, current_user, recurring_in
        )
    except ServiceError as exc:
        raise_http(exc)
    return recurring_service.to_detail(template)


@router.get("/{id}", response_model=schemas.RecurringTransactionDetail)
async def read_recurring_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a recurring transaction by ID.
    """
    _enrich(request, current_user, {"type": "get_recurring", "recurring_id": id})
    try:
        template = await recurring_service.get_recurring(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)
    return recurring_service.to_detail(template)


@router.put("/{id}", response_model=schemas.RecurringTransactionDetail)
async def update_recurring_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    recurring_in: schemas.RecurringTransactionUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update a recurring transaction. Only future occurrences change; pause or
    resume it with `is_active`.
    """
    _enrich(
        request,
        current_user,
        {
            "type": "update_recurring",
            "recurring_id": id,
            "fields": sorted(recurring_in.model_dump(exclude_unset=True)),
        },
    )
    try:
        template = await recurring_service.update_recurring(
            db, current_user, id, recurring_in
        )
    except ServiceError as exc:
        raise_http(exc)
    return recurring_service.to_detail(template)


@router.post("/{id}/skip", response_model=schemas.RecurringTransactionDetail)
async def skip_next_recurring_occurrence(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Skip the next occurrence without creating it.
    """
    _enrich(request, current_user, {"type": "skip_recurring", "recurring_id": id})
    try:
        template = await recurring_service.skip_next(db, current_user, id)
    except ServiceError as exc:
        raise_http(exc)
    return recurring_service.to_detail(template)


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_recurring_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    delete_generated: bool = False,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete a recurring transaction. Past transactions are kept unless
    `delete_generated` is true.
    """
    _enrich(
        request,
        current_user,
        {
            "type": "delete_recurring",
            "recurring_id": id,
            "delete_generated": delete_generated,
        },
    )
    try:
        await recurring_service.delete_recurring(
            db, current_user, id, delete_generated=delete_generated
        )
    except ServiceError as exc:
        raise_http(exc)
    return schemas.DeletionResponse(message=f"Recurring transaction {id} deleted")
