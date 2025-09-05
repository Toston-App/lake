from datetime import date
from typing import Optional

from pydantic import BaseModel, field_validator, validator


# Shared properties
class DataBase(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    date: Optional[date] = None
    account_id: Optional[int] = None
    category_id: Optional[int] = None
    subcategory_id: Optional[int] = None
    place_id: Optional[int] = None

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


# Properties to receive on Data creation
class DataCreate(DataBase):
    amount: float
    date: Optional[str] = None


# Properties to receive on Data update
class DataUpdate(DataBase):
    pass


# Properties shared by models stored in DB
class DataInDBBase(DataBase):
    id: int
    owner_id: int

    class Config:
        orm_mode = True


# Properties to return to client
class Data(DataInDBBase):
    date: Optional[date] = None

    class Config:
        orm_mode = True


# Properties properties stored in DB
class DataInDB(DataInDBBase):
    pass


class DeletionResponse(BaseModel):
    message: str
