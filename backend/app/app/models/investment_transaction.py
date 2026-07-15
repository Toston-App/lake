import enum
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_investment_tx_owner_idempotency"
        ),
        CheckConstraint("quantity > 0", name="ck_investment_tx_quantity_positive"),
        CheckConstraint(
            "price_per_unit >= 0", name="ck_investment_tx_price_nonnegative"
        ),
        CheckConstraint("fees >= 0", name="ck_investment_tx_fees_nonnegative"),
        CheckConstraint("quantity <= 1e15", name="ck_investment_tx_quantity_max"),
        CheckConstraint("price_per_unit <= 1e15", name="ck_investment_tx_price_max"),
        CheckConstraint("fees <= 1e15", name="ck_investment_tx_fees_max"),
        CheckConstraint("total_amount <= 1e30", name="ck_investment_tx_total_max"),
        CheckConstraint("total_amount >= 0", name="ck_investment_tx_total_nonnegative"),
        CheckConstraint(
            "exchange_rate_to_usd IS NULL OR (exchange_rate_to_usd > 0 AND exchange_rate_to_usd <= 1e6)",
            name="ck_investment_tx_usd_rate_positive",
        ),
        CheckConstraint(
            "exchange_rate_to_mxn IS NULL OR (exchange_rate_to_mxn > 0 AND exchange_rate_to_mxn <= 1e6)",
            name="ck_investment_tx_mxn_rate_positive",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)

    # Ownership and linking
    owner_id: int = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    account_id: int = Column(
        Integer, ForeignKey("account.id"), nullable=False, index=True
    )
    holding_id: int = Column(
        Integer, ForeignKey("holding.id"), nullable=False, index=True
    )

    # Transaction details
    transaction_type: TransactionType = Column(
        Enum(TransactionType), nullable=False, index=True
    )
    quantity: Decimal = Column(Numeric(28, 12), nullable=False)
    price_per_unit: Decimal = Column(Numeric(38, 8), nullable=False)

    # Currency and amounts
    currency: Currency = Column(Enum(Currency), nullable=False, default=Currency.USD)
    total_amount: Decimal = Column(Numeric(38, 8), nullable=False)
    fees: Decimal = Column(Numeric(38, 8), nullable=False, default=Decimal("0"))

    # Exchange rate at time of transaction (for multi-currency tracking)
    exchange_rate_to_usd: Decimal = Column(Numeric(20, 12), nullable=True)
    exchange_rate_to_mxn: Decimal = Column(Numeric(20, 12), nullable=True)

    # Metadata
    notes: str = Column(Text, nullable=True)
    idempotency_key: str = Column(String(128), nullable=True)
    request_fingerprint: str = Column(String(64), nullable=True)

    # When the transaction was executed (may differ from created_at)
    executed_at = Column(DateTime(timezone=True), nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    owner: "User" = relationship("User", back_populates="investment_transactions")
    account: "Account" = relationship(
        "Account", back_populates="investment_transactions"
    )
    holding: "Holding" = relationship("Holding", back_populates="transactions")
