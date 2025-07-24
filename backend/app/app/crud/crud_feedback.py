from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import CRUDBase
from app.models.feedback import Feedback
from app.schemas.feedback import FeedbackCreate, FeedbackUpdate


class CRUDFeedback(CRUDBase[Feedback, FeedbackCreate, FeedbackUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: FeedbackCreate, owner_id: int
    ) -> Feedback:
        db_obj = Feedback(**obj_in.dict(), owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj


feedback = CRUDFeedback(Feedback)
