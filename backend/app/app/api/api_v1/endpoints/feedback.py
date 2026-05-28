from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.services import feedback as feedback_service
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()

@router.post("", response_model=bool)
async def submit_feedback(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    feedback_in: schemas.FeedbackCreate = Body(...),
) -> bool:
    """
    Submit feedback
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "submit_feedback",
            "sentiment": feedback_in.sentiment,
        },
    )

    with timed() as t:
        result = await feedback_service.submit_feedback(
            db, current_user, feedback_in
        )

    enrich_event(
        request,
        telegram={
            "notification_sent": result,
            "duration_ms": t.ms,
        },
    )

    return result
