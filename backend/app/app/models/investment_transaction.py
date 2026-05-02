import enum
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base
from app.models.asset import Currency

if TYPE_CHECKING:
    from .account import Account
    from .holding import Holding
    from .user import User


class TransactionType(str, enum.Enum):
    """Types of investment transactions."""
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    SPLIT = "split"  # Stock split
    TRANSFER_IN = "transfer_in"  # Transfer from another account
    TRANSFER_OUT = "transfer_out"  # Transfer to another account


class InvestmentTransaction(Base):
    """
    Records individual buy/sell/dividend transactions for a holding.
    
    These transactions are used to calculate cost basis and track
    the history of a position.
    """
    __tablename__ = "investmenttransaction"
    
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    
    # Ownership and linking
    owner_id: int = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    account_id: int = Column(Integer, ForeignKey("account.id"), nullable=False, index=True)
    holding_id: int = Column(Integer, ForeignKey("holding.id"), nullable=False, index=True)
    
    # Transaction details
    transaction_type: TransactionType = Column(
        Enum(TransactionType), 
        nullable=False, 
        index=True
    )
    quantity: float = Column(Float, nullable=False)
    price_per_unit: float = Column(Float, nullable=False)
    
    # Currency and amounts
    currency: Currency = Column(Enum(Currency), nullable=False, default=Currency.USD)
    total_amount: float = Column(Float, nullable=False)  # quantity * price_per_unit
    fees: float = Column(Float, nullable=False, default=0.0)  # Account fees, commissions
    
    # Exchange rate at time of transaction (for multi-currency tracking)
    exchange_rate_to_usd: float = Column(Float, nullable=True)
    exchange_rate_to_mxn: float = Column(Float, nullable=True)
    
    # Metadata
    notes: str = Column(Text, nullable=True)
    
    # When the transaction was executed (may differ from created_at)
    executed_at = Column(DateTime(timezone=True), nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    owner: "User" = relationship("User", back_populates="investment_transactions")
    account: "Account" = relationship("Account", back_populates="investment_transactions")
    holding: "Holding" = relationship("Holding", back_populates="transactions")

