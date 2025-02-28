from datetime import datetime

from pydantic import BaseModel


# Shared properties
class AccountBase(BaseModel):
    name: str | None = None
    initial_balance: float | None = None
    current_balance: float | None = None
    total_expenses: float | None = None
    total_incomes: float | None = None
    total_transfers_in: float | None = None
    total_transfers_out: float | None = None


# Properties to receive on Account creation
class AccountCreate(AccountBase):
    name: str
    import_id: int | None = None


# Properties to receive on Account update
class AccountUpdate(AccountBase):
    updated_at: datetime | None = None


# Properties shared by models stored in DB
class AccountInDBBase(AccountBase):
    id: int
    owner_id: int
    import_id: int | None = None

    class Config:
        orm_mode = True


# Properties to return to client
class Account(AccountInDBBase):
    pass


# Properties properties stored in DB
class AccountInDB(AccountInDBBase):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DeletionResponse(BaseModel):
    message: str
