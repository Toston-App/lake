from typing import TYPE_CHECKING

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401


class Feedback(Base):
    id: int = Column(Integer, primary_key=True, index=True)
    message: str = Column(String, index=True)
    sentiment: str = Column(String, index=True, nullable=True)
    owner_id: int = Column(Integer, ForeignKey("user.id"))
    created_at: Date = Column(DateTime(timezone=True), server_default=func.now())
    owner = relationship("User", back_populates="feedbacks")
