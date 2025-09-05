from fastapi import APIRouter, Depends, Body
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.core.config import settings
from app import crud, models, schemas
from app.api import deps

router = APIRouter()

TELEGRAM_BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_OWNER_ID = settings.TELEGRAM_OWNER_ID


@router.post("", response_model=bool)
async def submit_feedback(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    feedback_in: schemas.FeedbackCreate = Body(...),
) -> bool:
    """
    Submit feedback
    """
    feedback = await crud.feedback.create_with_owner(
        db=db, obj_in=feedback_in, owner_id=current_user.id
    )

    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"text": f"Feedback received!\n\nMessage: {feedback.message}\nSentiment: {feedback.sentiment}\nFrom UserId: {current_user.id}", "chat_id": TELEGRAM_OWNER_ID},
        )

    return True
