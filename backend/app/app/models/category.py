from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .expense import Expense  # noqa: F401
    from .income import Income  # noqa: F401
    from .subcategory import Subcategory  # noqa: F401
    from .user import User  # noqa: F401


class Category(Base):
    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String, index=True, nullable=False)
    description: str = Column(String, index=True)
    color: str = Column(String, index=True, nullable=False)
    icon: str = Column(String, index=True)
    is_default: bool = Column(Boolean, index=True, nullable=False, default=False)
    is_income: bool = Column(Boolean, index=True, nullable=False, default=False)
    owner_id = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="categories")
    subcategories: list["Subcategory"] = relationship(
        "Subcategory", backref="parent_category", lazy="selectin"
    )
    expenses: list["Expense"] = relationship("Expense", back_populates="category")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
