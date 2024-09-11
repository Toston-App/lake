
from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer, String, Float, Date, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401
    from .account import Account  # noqa: F401
    from .place import Place  # noqa: F401
    from .category import Category  # noqa: F401
    from .subcategory import Subcategory  # noqa: F401

class Expense(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    amount: float = Column(Float, index=True, nullable=False)
    date: Date = Column(Date, index=True)
    description: str = Column(String, index=True)
    owner_id: int = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="expenses")
    account_id: int = Column(Integer, ForeignKey("account.id"))
    account: "Account" = relationship("Account", back_populates="expenses")
    category_id: int = Column(Integer, ForeignKey("category.id"))
    category: "Category" = relationship("Category", back_populates="expenses")
    subcategory_id: int = Column(Integer, ForeignKey("subcategory.id"))
    subcategory: "Subcategory" = relationship("Subcategory", back_populates="expenses")
    place_id: int = Column(Integer, ForeignKey("place.id"))
    place: "Place" = relationship("Place", back_populates="expenses")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
