from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


# Shared properties
class UserBase(BaseModel):
    uuid: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = True
    is_superuser: bool = False
    name: Optional[str] = None
    # Country code in Currency format - https://simplelocalize.io/data/locales/
    country: Optional[str] = None
    balance_total: Optional[float] = 0.0
    balance_income: Optional[float] = 0.0
    balance_outcome: Optional[float] = 0.0


# Properties to receive via API on creation
class UserCreate(UserBase):
    email: EmailStr
    password: str


class UserCreateUuid(BaseModel):
    uuid: str


# Properties to receive via API on update
class UserUpdate(UserBase):
    password: Optional[str] = None


class UserInDBBase(UserBase):
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        orm_mode = True


# Additional properties to return via API
class User(UserInDBBase):
    pass


# Additional properties stored in DB
class UserInDB(UserInDBBase):
    hashed_password: str
