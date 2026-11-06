import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.core.config import settings
from app.services import recurring as recurring_service
from app.services import recurring_detection as detection_service
from app.utilities.redis import hit_rate_limit
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()
logger = logging.getLogger(__name__)


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
    template = await recurring_service.create_recurring(db, current_user, recurring_in)
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
    return recurring_service.preview(preview_in)


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
    template = await recurring_service.create_from_transaction(
        db, current_user, recurring_in
    )
    return recurring_service.to_detail(template)


async def _enforce_rate_limit(key: str, limit: int) -> bool:
    """Count a hit against an hourly per-user limit; 429 when over it.

    Returns False when the limiter is unavailable (Redis down): the request
    goes ahead, and the caller decides what to drop to keep it cheap.
    """
    if limit <= 0:
        return True
    try:
        hits, retry_after = await hit_rate_limit(key, 3600)
    except Exception:
        logger.warning("Rate limiter unavailable for %s", key, exc_info=True)
        return False
    if hits > limit:
        raise HTTPException(
            status_code=429,
            detail={"reason": "rate_limited", "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    return True


@router.post("/detect", response_model=schemas.RecurringDetectionResult)
async def detect_recurring_transactions(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    detect_in: schemas.RecurringDetectRequest | None = Body(default=None),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Scan the transaction history for recurring series and suggest them.

    Nothing is created: accept the suggestions you want with
    `/suggestions/accept`. Transactions already in a series, series you
    already have and dismissed suggestions are skipped.

    Paid plans scan without limit. The free plan gets one scan that finds
    suggestions (402 after that); a scan that finds nothing doesn't count.
    The last result stays available at `GET /detect/last`.

    With `use_ai`, descriptions, place and category names, and each series'
    schedule and amounts are sent to the AI provider to group merchants and
    tell subscriptions from habits. Account names, ids and your email are
    never sent; descriptions are sent as written, so they may contain names.
    Without a working rate limiter the scan runs without the AI.
    """
    detect_in = detect_in or schemas.RecurringDetectRequest()
    _enrich(
        request, current_user, {"type": "recurring_detect", "use_ai": detect_in.use_ai}
    )
    # Access first: a free user who used their scan gets 402, not 429.
    unlimited = await detection_service.check_access(db, current_user)
    limited = await _enforce_rate_limit(
        f"recurring_detect:{current_user.id}",
        settings.RECURRING_DETECT_RATE_LIMIT_PER_HOUR,
    )
    # Unthrottled AI calls cost money: scan deterministically instead.
    use_ai = detect_in.use_ai and limited
    if detect_in.use_ai and not limited:
        enrich_event(request, operation={"ai_skipped": "rate_limiter_down"})
    with timed() as t:
        scan = await detection_service.run_scan(
            db, current_user, use_ai=use_ai, unlimited=unlimited
        )
    result = scan.result
    enrich_event(
        request,
        operation={
            "type": "recurring_detect",
            "duration_ms": t.ms,
            "scanned": result.scanned_transactions,
            "candidates": scan.candidates,
            "suggestions": len(result.suggestions),
            "ai_used": result.ai_used,
            "ai": scan.stats.as_dict(),
            "scans_remaining": result.scans_remaining,
        },
    )
    return result


@router.get("/detect/last", response_model=schemas.RecurringDetectionResult)
async def read_last_recurring_detection(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    The result of the last scan, without the suggestions accepted or
    dismissed since. 404 if you have never scanned.
    """
    _enrich(request, current_user, {"type": "recurring_detect_last"})
    return await detection_service.last_result(db, current_user)


@router.get("/detect/status", response_model=schemas.RecurringDetectionStatus)
async def read_recurring_detection_status(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Whether a scan is running. Poll it after a 409, or after a reload during
    a scan, then read the result from `GET /detect/last`. Not rate limited.
    """
    _enrich(request, current_user, {"type": "recurring_detect_status"})
    return await detection_service.scan_status(db, current_user)


@router.post(
    "/suggestions/accept",
    response_model=list[schemas.RecurringSuggestionAcceptResult],
)
async def accept_recurring_suggestions(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    accept_in: schemas.RecurringSuggestionAcceptRequest,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Turn suggestions into recurring transactions. Send each one back with
    any edits, using its latest occurrence as `transaction_id`. Each item
    succeeds or fails on its own.
    """
    _enrich(
        request,
        current_user,
        {"type": "recurring_accept_suggestions", "items": len(accept_in.items)},
    )
    # Each request re-runs detection over the history (no AI): throttle it.
    # Fails open; without AI it costs only database time.
    await _enforce_rate_limit(
        f"recurring_accept:{current_user.id}",
        settings.RECURRING_ACCEPT_RATE_LIMIT_PER_HOUR,
    )
    results = await detection_service.accept_suggestions(
        db, current_user, accept_in.items
    )
    enrich_event(
        request,
        operation={"created": sum(r.status == "created" for r in results)},
    )
    return results


@router.post(
    "/suggestions/dismiss",
    response_model=schemas.RecurringSuggestionDismissResult,
)
async def dismiss_recurring_suggestions(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    dismiss_in: schemas.RecurringSuggestionDismissRequest,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Hide suggestions for good. Dismissing one twice does nothing.
    """
    _enrich(
        request,
        current_user,
        {"type": "recurring_dismiss_suggestions", "items": len(dismiss_in.items)},
    )
    dismissed = await detection_service.dismiss_suggestions(
        db, current_user, dismiss_in.items
    )
    return schemas.RecurringSuggestionDismissResult(dismissed=dismissed)


@router.delete("/suggestions/dismissals/{fingerprint}", response_model=schemas.Msg)
async def undo_recurring_dismissal(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    fingerprint: str,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Undo a dismissal so the suggestion can come back on the next scan.
    """
    _enrich(request, current_user, {"type": "recurring_undo_dismissal"})
    await detection_service.undo_dismissal(db, current_user, fingerprint)
    return schemas.Msg(msg="Dismissal removed")


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
    template = await recurring_service.get_recurring(db, current_user, id)
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
    template = await recurring_service.update_recurring(
        db, current_user, id, recurring_in
    )
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
    template = await recurring_service.skip_next(db, current_user, id)
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
    `delete_generated` is true; the transaction that was marked as recurring
    to create the series is always kept.
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
    await recurring_service.delete_recurring(
        db, current_user, id, delete_generated=delete_generated
    )
    return schemas.DeletionResponse(message=f"Recurring transaction {id} deleted")
