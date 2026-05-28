"""Feedback services shared by API and chat."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.core.config import settings


async def submit_feedback(
    db: AsyncSession,
    user: models.User,
    feedback_in: schemas.FeedbackCreate,
) -> bool:
    feedback = await crud.feedback.create_with_owner(
        db=db, obj_in=feedback_in, owner_id=user.id
    )
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                "text": (
                    "Feedback received!\n\n"
                    f"Message: {feedback.message}\n"
                    f"Sentiment: {feedback.sentiment}\n"
                    f"From UserId: {user.id}"
                ),
                "chat_id": settings.TELEGRAM_OWNER_ID,
            },
        )
    return True
