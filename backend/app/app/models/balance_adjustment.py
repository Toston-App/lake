from typing import TYPE_CHECKING

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .account import Account  # noqa: F401
    from .user import User  # noqa: F401


class BalanceAdjustment(Base):
    """
    Model to track manual balance adjustments made by users.
    This allows users to manually adjust their account balance when it doesn't match
    the real balance (e.g., after reconciling with bank statements).
    """
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    
    # Foreign keys
    account_id: int = Column(Integer, ForeignKey("account.id"), nullable=False, index=True)
    user_id: int = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    
    # Balance information
    old_balance: float = Column(Float, nullable=False)
    new_balance: float = Column(Float, nullable=False)
    adjustment_amount: float = Column(Float, nullable=False)  # new_balance - old_balance
    
    # Description and timestamps
    description: str = Column(Text, nullable=True)
    adjustment_date: Date = Column(Date, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    account: "Account" = relationship("Account", back_populates="balance_adjustments")
    user: "User" = relationship("User", back_populates="balance_adjustments")
