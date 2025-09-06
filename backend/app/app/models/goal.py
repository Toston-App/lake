from typing import TYPE_CHECKING
from enum import Enum

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .account import Account  # noqa: F401
    from .user import User  # noqa: F401
    from .expense import Expense  # noqa: F401
    from .transfer import Transfer  # noqa: F401


class GoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    OVERDUE = "overdue"
    CLOSED = "closed"


class Goal(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    name: str = Column(String, index=True, nullable=False)
    description: str = Column(String, nullable=True)
    target_amount: float = Column(Float, nullable=False)
    current_amount: float = Column(Float, default=0.0, nullable=False)
    start_date: Date = Column(Date, nullable=True)
    deadline: Date = Column(Date, nullable=True)
    status: GoalStatus = Column(SQLEnum(GoalStatus), default=GoalStatus.ACTIVE, nullable=False)
    completed_at: DateTime = Column(DateTime(timezone=True), nullable=True)
    
    owner_id: int = Column(Integer, ForeignKey("user.id"), nullable=False)
    owner: "User" = relationship("User", back_populates="goals")
    
    linked_account_id: int = Column(Integer, ForeignKey("account.id"), nullable=True)
    linked_account: "Account" = relationship("Account", back_populates="goals")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    expenses: list["Expense"] = relationship("Expense", back_populates="goal")
    transfers: list["Transfer"] = relationship("Transfer", back_populates="goal")