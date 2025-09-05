from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, field_validator, validator


# Shared properties
class GoalBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target_amount: Optional[float] = None
    current_amount: Optional[float] = 0.0
    target_date: Optional[date] = None
    is_completed: Optional[bool] = False
    account_id: Optional[int] = None

    # Fix the amounts to 2 decimal places
    @field_validator('target_amount', 'current_amount')
    @classmethod
    def round_amount(cls, v: float) -> float:
        if v is not None:
            return round(v, 2)
        return v

    # Validate that amounts are positive
    @validator("target_amount", "current_amount", pre=True, always=True)
    def amount_must_be_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("Amount must be positive")
        return v


# Properties to receive on Goal creation
class GoalCreate(GoalBase):
    name: str
    target_amount: float
    target_date: Optional[date] = None


# Properties to receive on Goal update
class GoalUpdate(GoalBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class GoalInDBBase(GoalBase):
    id: int
    owner_id: int

    class Config:
        orm_mode = True


# Properties to return to client
class Goal(GoalInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def progress_percentage(self) -> float:
        if self.target_amount and self.target_amount > 0:
            return min(round((self.current_amount / self.target_amount) * 100, 2), 100.0)
        return 0.0

    class Config:
        orm_mode = True


# Properties properties stored in DB
class GoalInDB(GoalInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DeletionResponse(BaseModel):
    message: str


class BulkDeletionResponse(BaseModel):
    message: str
    deleted_ids: list[int]