from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, field_validator, validator


# Shared properties
class TransferBase(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    date: Optional[date]
    from_acc: Optional[int] = None
    to_acc: Optional[int] = None

    # Fix the amount to 2 decimal places
    @field_validator('amount')
    @classmethod
    def round_amount(cls, v: float) -> float:
        # 'v' is the value of the 'amount' field
        if v is not None:
            return round(v, 2)
        return v

    # Validate that the amount is positive
    @validator("amount", pre=True, always=True)
    def amount_must_be_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("Amount must be positive")
        return v


# Properties to receive on Transfer creation
class TransferCreate(TransferBase):
    amount: float
    from_acc: int
    to_acc: int


# Properties to receive on Transfer update
class TransferUpdate(TransferBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class TransferInDBBase(TransferBase):
    id: int
    owner_id: int

    class Config:
        orm_mode = True


# Properties to return to client
class Transfer(TransferInDBBase):
    date: Optional[date]

    class Config:
        orm_mode = True


# Properties properties stored in DB
class TransferInDB(TransferInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DeletionResponse(BaseModel):
    message: str
