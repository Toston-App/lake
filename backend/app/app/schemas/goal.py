from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, field_validator, validator

from app.models.goal import GoalCategory, GoalStatus


# Shared properties
class GoalBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target_amount: Optional[float] = None
    current_amount: Optional[float] = None
    category: Optional[GoalCategory] = None
    status: Optional[GoalStatus] = None
    target_date: Optional[date] = None

    # Fix the amounts to 2 decimal places
    @field_validator('target_amount', 'current_amount')
    @classmethod
    def round_amount(cls, v: float) -> float:
        if v is not None:
            return round(v, 2)
        return v

    # Validate that the amounts are positive
    @validator("target_amount", "current_amount", pre=True, always=True)
    def amount_must_be_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("Amount must be positive")
        return v


# Properties to receive on Goal creation
class GoalCreate(GoalBase):
    name: str
    target_amount: float
    category: GoalCategory = GoalCategory.OTHER
    status: GoalStatus = GoalStatus.ACTIVE
    current_amount: float = 0.0
    target_date: Optional[date] = None


# Properties to receive on Goal update
class GoalUpdate(GoalBase):
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# Properties shared by models stored in DB
class GoalInDBBase(GoalBase):
    id: int
    owner_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        orm_mode = True


# Properties to return to client
class Goal(GoalInDBBase):
    progress_percentage: float
    remaining_amount: float
    is_completed: bool

    class Config:
        orm_mode = True


# Properties properties stored in DB
class GoalInDB(GoalInDBBase):
    pass


class GoalStats(BaseModel):
    total_goals: int
    active_goals: int
    completed_goals: int
    total_target_amount: float
    total_current_amount: float
    average_progress: float


class DeletionResponse(BaseModel):
    message: str


class BulkDeletionResponse(BaseModel):
    message: str
    deleted_ids: list[int]