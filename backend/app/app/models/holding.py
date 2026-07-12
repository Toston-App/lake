from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Column, DateTime, Enum, Float, ForeignKey, Integer, UniqueConstraint
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
    )
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    
    # Ownership
    owner_id: int = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    account_id: int = Column(Integer, ForeignKey("account.id"), nullable=False, index=True)
    asset_id: int = Column(Integer, ForeignKey("asset.id"), nullable=False, index=True)
    
    # Position details
    quantity: float = Column(Float, nullable=False, default=0.0)
    
    # Cost basis tracking (in the currency the asset was purchased)
    avg_cost_basis: float = Column(Float, nullable=False, default=0.0)  # Average cost per unit
    cost_currency: Currency = Column(
        Enum(Currency), 
        nullable=False, 
        default=Currency.USD
    )
    total_invested: float = Column(Float, nullable=False, default=0.0)  # Total amount invested
    
    # Current valuation (updated when prices refresh)
    current_value: float = Column(Float, nullable=False, default=0.0)
    current_value_mxn: float = Column(Float, nullable=False, default=0.0)
    current_value_usd: float = Column(Float, nullable=False, default=0.0)
    
    # Performance metrics
    unrealized_gain_loss: float = Column(Float, nullable=False, default=0.0)
    unrealized_gain_loss_pct: float = Column(Float, nullable=False, default=0.0)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    owner: "User" = relationship("User", back_populates="holdings")
    account: "Account" = relationship("Account", back_populates="holdings")
    asset: "Asset" = relationship("Asset", back_populates="holdings")
    transactions: list["InvestmentTransaction"] = relationship(
        "InvestmentTransaction", 
        back_populates="holding",
        cascade="all, delete-orphan"
    )
