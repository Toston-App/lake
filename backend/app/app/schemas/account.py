from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# Shared properties
class AccountBase(BaseModel):
    name: Optional[str] = None
    initial_balance: Optional[float] = None
    current_balance : Optional[float] = None
    total_expenses: Optional[float] = None
    total_incomes: Optional[float] = None
    total_transfers_in: Optional[float] = None
    total_transfers_out: Optional[float] = None

# Properties to receive on Account creation
class AccountCreate(AccountBase):
    name: str
    import_id: Optional[int] = None


# Properties to receive on Account update
class AccountUpdate(AccountBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class AccountInDBBase(AccountBase):
    id: int
    owner_id: int
    import_id: Optional[int] = None

    class Config:
        orm_mode = True


# Properties to return to client
class Account(AccountInDBBase):
    pass


# Properties properties stored in DB
class AccountInDB(AccountInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class DeletionResponse(BaseModel):
    message: str
