from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.core.config import settings
from app import crud, models, schemas
from app.api import deps
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()

TELEGRAM_BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_OWNER_ID = settings.TELEGRAM_OWNER_ID


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

    feedback = await crud.feedback.create_with_owner(
        db=db, obj_in=feedback_in, owner_id=current_user.id
    )

    with timed() as t:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"text": f"Feedback received!\n\nMessage: {feedback.message}\nSentiment: {feedback.sentiment}\nFrom UserId: {current_user.id}", "chat_id": TELEGRAM_OWNER_ID},
            )

    enrich_event(
        request,
        telegram={
            "notification_sent": resp.status_code == 200,
            "duration_ms": t.ms,
        },
    )

    return True
