from datetime import datetime

from pydantic import BaseModel, EmailStr


# Shared properties
class UserBase(BaseModel):
    uuid: str | None = None
    email: EmailStr | None = None
    is_active: bool | None = True
    is_superuser: bool = False
    name: str | None = None
    # Country code in Currency format - https://simplelocalize.io/data/locales/
    country: str | None = None
    balance_total: float | None = 0.0
    balance_income: float | None = 0.0
    balance_outcome: float | None = 0.0


# Properties to receive via API on creation
class UserCreate(UserBase):
    email: EmailStr
    password: str


class UserCreateUuid(BaseModel):
    uuid: str


# Properties to receive via API on update
class UserUpdate(UserBase):
    password: str | None = None


class UserInDBBase(UserBase):
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        orm_mode = True


# Additional properties to return via API
class User(UserInDBBase):
    pass


# Additional properties stored in DB
class UserInDB(UserInDBBase):
    hashed_password: str
