from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401


class Feedback(Base):
    id: int = Column(Integer, primary_key=True, index=True)
    message: str = Column(String, index=True)
    sentiment: str = Column(String, index=True, nullable=True)
    owner_id = Column(Integer, ForeignKey("user.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    owner: "User" = relationship("User", back_populates="feedback")
