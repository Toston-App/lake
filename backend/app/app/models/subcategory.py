from typing import TYPE_CHECKING, List

from sqlalchemy import Column, ForeignKey, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401
    from .expense import Expense  # noqa: F401
    from .income import Income  # noqa: F401


class Subcategory(Base):
    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String, index=True, nullable=False)
    description: str = Column(String, index=True)
    icon: str = Column(String, index=True)
    is_default: bool = Column(Boolean, index=True, nullable=False, default=False)
    owner_id: int = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="subcategories")
    category_id: int = Column(Integer, ForeignKey("category.id"))
    expenses: List["Expense"] = relationship("Expense", back_populates="subcategory")
    incomes: List["Income"] = relationship("Income", back_populates="subcategory")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())