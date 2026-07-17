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
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base
from app.models.asset import Currency

if TYPE_CHECKING:
    from .account import Account
    from .asset import Asset
    from .investment_transaction import InvestmentTransaction
    from .user import User


class Holding(Base):
    """
    Represents a user's position in an asset.

    Each holding tracks the quantity owned, cost basis, and current value
    for performance tracking.
    """

    __table_args__ = (
        UniqueConstraint("account_id", "asset_id", name="uq_holding_account_asset"),
        CheckConstraint("quantity >= 0", name="ck_holding_quantity_nonnegative"),
        CheckConstraint("avg_cost_basis >= 0", name="ck_holding_cost_nonnegative"),
        CheckConstraint("total_invested >= 0", name="ck_holding_total_nonnegative"),
        CheckConstraint("quantity <= 1e15", name="ck_holding_quantity_max"),
        CheckConstraint("avg_cost_basis <= 1e15", name="ck_holding_cost_max"),
        CheckConstraint("total_invested <= 1e30", name="ck_holding_total_max"),
        CheckConstraint(
            "current_value >= 0 AND current_value <= 1e30",
            name="ck_holding_current_value_range",
        ),
        CheckConstraint(
            "current_value_usd >= 0 AND current_value_usd <= 1e30",
            name="ck_holding_current_value_usd_range",
        ),
        CheckConstraint(
            "current_value_mxn >= 0 AND current_value_mxn <= 1e30",
            name="ck_holding_current_value_mxn_range",
        ),
    )
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)

    # Ownership
    owner_id: int = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    account_id: int = Column(
        Integer, ForeignKey("account.id"), nullable=False, index=True
    )
    asset_id: int = Column(Integer, ForeignKey("asset.id"), nullable=False, index=True)

    # Position details
    quantity: Decimal = Column(Numeric(28, 12), nullable=False, default=Decimal("0"))

    # Cost basis tracking (in the currency the asset was purchased)
    avg_cost_basis: Decimal = Column(
        Numeric(38, 8), nullable=False, default=Decimal("0")
    )
    cost_currency: Currency = Column(
        Enum(Currency), nullable=False, default=Currency.USD
    )
    total_invested: Decimal = Column(
        Numeric(38, 8), nullable=False, default=Decimal("0")
    )

    # Current valuation (updated when prices refresh)
    current_value: Decimal = Column(
        Numeric(38, 8), nullable=False, default=Decimal("0")
    )
    current_value_mxn: Decimal = Column(
        Numeric(38, 8), nullable=False, default=Decimal("0")
    )
    current_value_usd: Decimal = Column(
        Numeric(38, 8), nullable=False, default=Decimal("0")
    )

    # Performance metrics
    unrealized_gain_loss: Decimal = Column(
        Numeric(38, 8), nullable=False, default=Decimal("0")
    )
    unrealized_gain_loss_pct: Decimal = Column(
        Numeric(20, 12), nullable=False, default=Decimal("0")
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    owner: "User" = relationship("User", back_populates="holdings")
    account: "Account" = relationship("Account", back_populates="holdings")
    asset: "Asset" = relationship("Asset", back_populates="holdings")
    transactions: list["InvestmentTransaction"] = relationship(
        "InvestmentTransaction", back_populates="holding", cascade="all, delete-orphan"
    )
