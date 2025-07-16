import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, validator

from .subcategory import Subcategory  # noqa: F401


# Shared properties
class CategoryBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    is_default: bool = False
    is_income: bool = False
    total: float = 0.0

    @validator("name", "color")
    def validate_non_empty_string(cls, value):
        if isinstance(value, str) and value.strip() == "":
            raise ValueError("Field cannot be an empty string")
        return value

    @validator("color")
    def validate_hex_color(cls, value):
        hex_color_pattern = r"^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$"
        if not re.match(hex_color_pattern, value):
            raise ValueError(
                "Color must be a valid hexadecimal color code (e.g., #RRGGBB)"
            )
        return value


# Properties to receive on category creation
class CategoryCreate(CategoryBase):
    name: str
    color: str


# Properties to receive on category update
class CategoryUpdate(CategoryBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class CategoryInDBBase(CategoryBase):
    id: int
    owner_id: int
    subcategories: list["Subcategory"] = []

    class Config:
        from_attributes = True


# Properties to return to client
class Category(CategoryInDBBase):
    pass

    class Config:
        from_attributes = True

# Properties properties stored in DB
class CategoryInDB(CategoryInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DeletionResponse(BaseModel):
    message: str
