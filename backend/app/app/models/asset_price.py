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
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base
from app.models.asset import Currency

if TYPE_CHECKING:
    from .asset import Asset


class AssetPrice(Base):
    """
    Caches price data for assets.

    Stores prices in both the native currency and converted to USD/MXN
    for easy portfolio valuation in either currency.
    """

    __tablename__ = "assetprice"
    __table_args__ = (
        CheckConstraint(
            "price > 0 AND price <= 1e15", name="ck_asset_price_native_range"
        ),
        CheckConstraint(
            "price_usd > 0 AND price_usd <= 1e15", name="ck_asset_price_usd_range"
        ),
        CheckConstraint(
            "price_mxn > 0 AND price_mxn <= 1e15", name="ck_asset_price_mxn_range"
        ),
        CheckConstraint(
            "volume IS NULL OR volume >= 0", name="ck_asset_price_volume_nonnegative"
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)

    # Asset reference
    asset_id: int = Column(Integer, ForeignKey("asset.id"), nullable=False, index=True)

    # Price in native currency
    price: Decimal = Column(Numeric(38, 8), nullable=False)
    currency: Currency = Column(Enum(Currency), nullable=False)

    # Converted prices for portfolio calculations
    price_usd: Decimal = Column(Numeric(38, 8), nullable=False)
    price_mxn: Decimal = Column(Numeric(38, 8), nullable=False)

    # Market data
    open_price: Decimal = Column(Numeric(38, 8), nullable=True)
    high_price: Decimal = Column(Numeric(38, 8), nullable=True)
    low_price: Decimal = Column(Numeric(38, 8), nullable=True)
    previous_close: Decimal = Column(Numeric(38, 8), nullable=True)
    volume: Decimal = Column(Numeric(38, 8), nullable=True)

    # Change metrics
    change: Decimal = Column(Numeric(38, 8), nullable=True)
    change_percent: Decimal = Column(Numeric(20, 12), nullable=True)

    # When this price was fetched from the API
    fetched_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    asset: "Asset" = relationship("Asset", back_populates="prices")
