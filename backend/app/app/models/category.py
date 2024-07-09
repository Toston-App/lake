from typing import TYPE_CHECKING, List

from sqlalchemy import Column, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401
    from .subcategory import Subcategory  # noqa: F401
    from .expense import Expense  # noqa: F401
    from .income import Income  # noqa: F401

class Category(Base):
    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String, index=True, nullable=False)
    description: str = Column(String, index=True)
    color: str = Column(String, index=True, nullable=False)
    icon: str = Column(String, index=True)
    is_default: bool = Column(Boolean, index=True, nullable=False, default=False)
    owner_id = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="categories")
    subcategories: List["Subcategory"] = relationship("Subcategory", backref="parent_category", lazy="selectin")
    expenses: List["Expense"] = relationship("Expense", back_populates="category")