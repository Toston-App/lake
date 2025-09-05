from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, field_validator, validator


# Shared properties
class ExpenseBase(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    date: Optional[date] = None
    account_id: Optional[int] = None
    category_id: Optional[int] = None
    subcategory_id: Optional[int] = None
    place_id: Optional[int] = None
    made_from: Optional[str] = "Web"

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

    @validator("made_from")
    def validate_made_from(cls, v):
        if v not in ["Web", "WhatsApp", "OCR"]:
            raise ValueError("made_from must be one of: Web, WhatsApp, OCR")
        return v

# Properties to receive on Expense creation
class ExpenseCreate(ExpenseBase):
    amount: float
    date: Optional[str] = None
    import_id: Optional[int] = None


# Properties to receive on Expense update
class ExpenseUpdate(ExpenseBase):
    date: Optional[str] = None
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class ExpenseInDBBase(ExpenseBase):
    id: int
    owner_id: int
    import_id: Optional[int] = None

    class Config:
        orm_mode = True


# Properties to return to client
class Expense(ExpenseInDBBase):
    date: Optional[date]

    class Config:
        orm_mode = True


# Properties properties stored in DB
class ExpenseInDB(ExpenseInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DeletionResponse(BaseModel):
    message: str


class BulkDeletionResponse(BaseModel):
    message: str
    deleted_ids: list[int]
