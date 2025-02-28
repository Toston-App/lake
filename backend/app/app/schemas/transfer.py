from datetime import date, datetime

from pydantic import BaseModel, root_validator, validator


# Shared properties
class TransferBase(BaseModel):
    description: str | None = None
    amount: float | None = None
    date: date | None
    from_acc: int | None = None
    to_acc: int | None = None

    # Fix the amount to 2 decimal places
    @root_validator
    def round_amount(cls, values):
        amount = values.get("amount")
        if amount is not None:
            values["amount"] = round(amount, 2)
        return values

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
    updated_at: datetime | None = None


# Properties shared by models stored in DB
class TransferInDBBase(TransferBase):
    id: int
    owner_id: int

    class Config:
        orm_mode = True


# Properties to return to client
class Transfer(TransferInDBBase):
    date: date | None

    class Config:
        orm_mode = True


# Properties properties stored in DB
class TransferInDB(TransferInDBBase):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DeletionResponse(BaseModel):
    message: str
