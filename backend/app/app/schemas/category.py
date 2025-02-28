import re
from datetime import datetime

from pydantic import BaseModel, validator

from .subcategory import Subcategory  # noqa: F401


# Shared properties
class CategoryBase(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    is_default: bool = False
    is_income: bool = False

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
    pass


# Properties shared by models stored in DB
class CategoryInDBBase(CategoryBase):
    id: int
    owner_id: int
    subcategories: list["Subcategory"] = []

    class Config:
        orm_mode = True


# Properties to return to client
class Category(CategoryInDBBase):
    pass


# Properties properties stored in DB
class CategoryInDB(CategoryInDBBase):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DeletionResponse(BaseModel):
    message: str
