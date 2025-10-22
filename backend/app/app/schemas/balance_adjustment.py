from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, validator


# Shared properties
class BalanceAdjustmentBase(BaseModel):
    description: Optional[str] = None
    adjustment_date: Optional[date] = None


# Properties to receive on BalanceAdjustment creation
class BalanceAdjustmentCreate(BalanceAdjustmentBase):
    account_id: int
    new_balance: float
    adjustment_date: date = Field(default_factory=date.today)

    @validator('new_balance')
    def validate_new_balance(cls, v):
        if v is None:
            raise ValueError('new_balance is required')
        return v


# Properties to receive on BalanceAdjustment update
class BalanceAdjustmentUpdate(BalanceAdjustmentBase):
    pass


# Properties shared by models stored in DB
class BalanceAdjustmentInDBBase(BalanceAdjustmentBase):
    id: int
    account_id: int
    owner_id: int
    old_balance: float
    new_balance: float
    adjustment_amount: float
    adjustment_date: date
    created_at: datetime

    class Config:
        orm_mode = True


# Properties to return to client
class BalanceAdjustment(BalanceAdjustmentInDBBase):
    pass


# Properties stored in DB
class BalanceAdjustmentInDB(BalanceAdjustmentInDBBase):
    pass
