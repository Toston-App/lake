import uuid
from typing import TYPE_CHECKING, List

from sqlalchemy import ForeignKey, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func


from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401
    from .expense import Expense  # noqa: F401
    from .income import Income  # noqa: F401
    from .transfer import Transfer  # noqa: F401


class Account(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    name: str = Column(String, index=True, nullable=False)
    initial_balance: float = Column(Float, index=True, default=0.0)
    current_balance: float = Column(Float, index=True, default=0.0)
    total_expenses: float = Column(Float, index=True, default=0.0)
    total_incomes: float = Column(Float, index=True, default=0.0)
    total_transfers_in: float = Column(Float, index=True, default=0.0)
    total_transfers_out: float = Column(Float, index=True, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    owner_id = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship("User", back_populates="accounts")
    expenses: List["Expense"] = relationship("Expense", back_populates="account")
    incomes: List["Income"] = relationship("Income", back_populates="account")
    transfers_in: List["Transfer"] = relationship("Transfer", foreign_keys="[Transfer.to_acc]", back_populates="account_to")
    transfers_out: List["Transfer"] = relationship("Transfer", foreign_keys="[Transfer.from_acc]", back_populates="account_from")
    import_id: int = Column(Integer, ForeignKey("import.id"))
    import_source = relationship("Import", back_populates="accounts")