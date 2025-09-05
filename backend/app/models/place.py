from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .expense import Expense  # noqa: F401
    from .income import Income  # noqa: F401
    from .user import User  # noqa: F401


class Place(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    name: str = Column(String, index=True)
    is_online: bool = Column(Boolean, index=True, default=True)
    owner_id: int = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="places")
    expenses: list["Expense"] = relationship("Expense", back_populates="place")
    incomes: list["Income"] = relationship("Income", back_populates="place")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    import_id: int = Column(Integer, ForeignKey("import.id"))
