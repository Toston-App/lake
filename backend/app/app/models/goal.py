import enum
from typing import TYPE_CHECKING

from sqlalchemy import Column, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401


class GoalStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class GoalCategory(str, enum.Enum):
    EMERGENCY_FUND = "Emergency Fund"
    VACATION = "Vacation"
    CAR = "Car"
    HOUSE = "House"
    EDUCATION = "Education"
    INVESTMENT = "Investment"
    DEBT_PAYOFF = "Debt Payoff"
    WEDDING = "Wedding"
    RETIREMENT = "Retirement"
    OTHER = "Other"


class Goal(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    name: str = Column(String, index=True, nullable=False)
    description: str = Column(Text, nullable=True)
    target_amount: float = Column(Float, index=True, nullable=False)
    current_amount: float = Column(Float, index=True, default=0.0)
    category: GoalCategory = Column(Enum(GoalCategory), index=True, nullable=False, default=GoalCategory.OTHER)
    status: GoalStatus = Column(Enum(GoalStatus), index=True, nullable=False, default=GoalStatus.ACTIVE)
    target_date: Date = Column(Date, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    owner_id: int = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="goals")

    @property
    def progress_percentage(self) -> float:
        if self.target_amount == 0:
            return 0.0
        return min(100.0, (self.current_amount / self.target_amount) * 100.0)

    @property
    def remaining_amount(self) -> float:
        return max(0.0, self.target_amount - self.current_amount)

    @property
    def is_completed(self) -> bool:
        return self.current_amount >= self.target_amount