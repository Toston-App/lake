from typing import TYPE_CHECKING, List

from sqlalchemy import Boolean, Column, Integer, String, Float
from sqlalchemy.orm import relationship

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
    name: str = Column(String, index=True, nullable=False)
    email: str = Column(String, unique=True, index=True, nullable=False)
    country: str = Column(String, index=True, nullable=False)
    hashed_password: str = Column(String, nullable=False)
    is_active: bool = Column(Boolean(), default=True)
    is_superuser: bool = Column(Boolean(), default=False)
    balance_total: float = Column(Float, default=0.0)
    balance_income: float = Column(Float, default=0.0)
    balance_outcome: float = Column(Float, default=0.0)
    items: List["Item"] = relationship("Item", back_populates="owner")
    places: List["Place"] = relationship("Place", back_populates="owner")
    expenses: List["Expense"] = relationship("Expense", back_populates="owner")
    incomes: List["Income"] = relationship("Income", back_populates="owner")
    transfers: List["Transfer"] = relationship("Transfer", back_populates="owner")
    accounts: List["Account"] = relationship("Account", back_populates="owner")
    categories: List["Category"] = relationship("Category", back_populates="owner")
    subcategories: List["Subcategory"] = relationship("Subcategory", back_populates="owner")