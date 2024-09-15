from typing import TYPE_CHECKING, List

from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .item import Item  # noqa: F401
    from .place import Place  # noqa: F401
    from .expense import Expense  # noqa: F401
    from .income import Income  # noqa: F401
    from .transfer import Transfer  # noqa: F401
    from .account import Account  # noqa: F401
    from .category import Category  # noqa: F401
    from .subcategory import Subcategory  # noqa: F401


class User(Base):
    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String, index=True, nullable=True)
    email: str = Column(String, unique=True, index=True, nullable=True)
    uuid: str = Column(String, unique=True, index=True, nullable=True)
    # Country code in Currency format - https://simplelocalize.io/data/locales/
    country: str = Column(String, index=True, nullable=False)
    hashed_password: str = Column(String, nullable=True)
    is_active: bool = Column(Boolean(), default=True)
    is_superuser: bool = Column(Boolean(), default=False)
    balance_total: float = Column(Float, default=0.0)
    balance_income: float = Column(Float, default=0.0)
    balance_outcome: float = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    items: List["Item"] = relationship("Item", back_populates="owner", cascade="all, delete-orphan")
    places: List["Place"] = relationship("Place", back_populates="owner", cascade="all, delete-orphan")
    expenses: List["Expense"] = relationship("Expense", back_populates="owner", cascade="all, delete-orphan")
    incomes: List["Income"] = relationship("Income", back_populates="owner", cascade="all, delete-orphan")
    transfers: List["Transfer"] = relationship("Transfer", back_populates="owner", cascade="all, delete-orphan")
    accounts: List["Account"] = relationship("Account", back_populates="owner", cascade="all, delete-orphan")
    categories: List["Category"] = relationship("Category", back_populates="owner", cascade="all, delete-orphan")
    subcategories: List["Subcategory"] = relationship("Subcategory", back_populates="owner", cascade="all, delete-orphan")