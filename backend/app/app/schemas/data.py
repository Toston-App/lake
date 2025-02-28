from datetime import date

from pydantic import BaseModel, root_validator, validator


# Shared properties
class DataBase(BaseModel):
    description: str | None = None
    amount: float | None = None
    date: date | None = None
    account_id: int | None = None
    category_id: int | None = None
    subcategory_id: int | None = None
    place_id: int | None = None

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


# Properties to receive on Data creation
class DataCreate(DataBase):
    amount: float
    date: str | None = None


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
    date: date | None = None

    class Config:
        orm_mode = True


# Properties properties stored in DB
class DataInDB(DataInDBBase):
    pass


class DeletionResponse(BaseModel):
    message: str
