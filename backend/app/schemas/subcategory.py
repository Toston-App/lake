from datetime import datetime
from typing import Optional

from pydantic import BaseModel, validator


# Shared properties
class SubcategoryBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    is_default: bool = False
    total: float = 0.0

    @validator("name")
    def validate_non_empty_string(cls, value):
        if isinstance(value, str) and value.strip() == "":
            raise ValueError("Field cannot be an empty string")
        return value


# Properties to receive on subcategory creation
class SubcategoryCreate(SubcategoryBase):
    name: str
    category_id: int


# Properties to receive on subcategory update
class SubcategoryUpdate(SubcategoryBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class SubcategoryInDBBase(SubcategoryBase):
    id: int
    owner_id: int
    category_id: int

    class Config:
        orm_mode = True


# Properties to return to client
class Subcategory(SubcategoryInDBBase):
    pass


# Properties properties stored in DB
class SubcategoryInDB(SubcategoryInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DeletionResponse(BaseModel):
    message: str
