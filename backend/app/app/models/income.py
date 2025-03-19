from typing import TYPE_CHECKING

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .account import Account  # noqa: F401
    from .place import Place  # noqa: F401
    from .subcategory import Subcategory  # noqa: F401
    from .user import User  # noqa: F401


class Income(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    amount: float = Column(Float, index=True, nullable=False)
    date: Date = Column(Date, index=True)
    description: str = Column(String, index=True)
    owner_id = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="incomes")
    account_id: int = Column(Integer, ForeignKey("account.id"))
    account: "Account" = relationship("Account", back_populates="incomes")
    subcategory_id: int = Column(Integer, ForeignKey("subcategory.id"))
    subcategory: "Subcategory" = relationship("Subcategory", back_populates="incomes")
    place_id: int = Column(Integer, ForeignKey("place.id"))
    place: "Place" = relationship("Place", back_populates="incomes")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    import_id: int = Column(Integer, ForeignKey("import.id"))
    made_from: str = Column(String, default="Web") # Web, WhatsApp, OCR
