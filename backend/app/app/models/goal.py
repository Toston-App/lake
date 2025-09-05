from typing import TYPE_CHECKING

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .account import Account  # noqa: F401
    from .user import User  # noqa: F401


class Goal(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    name: str = Column(String, index=True, nullable=False)
    description: str = Column(String, index=True)
    target_amount: float = Column(Float, nullable=False)
    current_amount: float = Column(Float, default=0.0)
    target_date: Date = Column(Date, index=True)
    is_completed: bool = Column(Boolean, default=False)
    owner_id: int = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="goals")
    account_id: int = Column(Integer, ForeignKey("account.id"), nullable=True)
    account: "Account" = relationship("Account", back_populates="goals")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())