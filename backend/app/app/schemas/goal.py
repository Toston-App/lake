from datetime import date, datetime
from typing import Optional
from enum import Enum

from pydantic import BaseModel, field_validator, validator


class GoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    OVERDUE = "overdue"
    CLOSED = "closed"


# Shared properties
class GoalBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target_amount: Optional[float] = None
    current_amount: Optional[float] = 0.0
    start_date: Optional[date] = None
    deadline: Optional[date] = None
    status: Optional[GoalStatus] = GoalStatus.ACTIVE
    linked_account_id: Optional[int] = None

    # Coerce datetimes / ISO strings with time to plain dates
    @field_validator('start_date', 'deadline', mode='before')
    @classmethod
    def coerce_datetime_to_date(cls, v):
        if v is None:
            return v
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, str):
            txt = v.strip()
            # Remove Z timezone marker for fromisoformat compatibility
            if txt.endswith('Z'):
                txt = txt[:-1]
            try:
                if 'T' in txt:
                    # Has time component -> parse as datetime then drop time
                    return datetime.fromisoformat(txt).date()
                # Pure date string
                return date.fromisoformat(txt)
            except ValueError:
                raise ValueError('Invalid date/datetime format for date field')
        raise ValueError('Unsupported type for date field')

    # Fix amounts to 2 decimal places
    @field_validator('target_amount', 'current_amount')
    @classmethod
    def round_amounts(cls, v: float) -> float:
        if v is not None:
            return round(v, 2)
        return v

    # Validate that target_amount is positive
    @validator("target_amount", pre=True, always=True)
    def target_amount_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Target amount must be positive")
        return v


# Properties to receive on Goal creation
class GoalCreate(GoalBase):
    name: str
    target_amount: float


# Properties to receive on Goal update
class GoalUpdate(GoalBase):
    completed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class GoalInDBBase(GoalBase):
    id: int
    owner_id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        orm_mode = True


# Properties to return to client
class Goal(GoalInDBBase):
    pass


# Properties stored in DB
class GoalInDB(GoalInDBBase):
    pass


class DeletionResponse(BaseModel):
    message: str