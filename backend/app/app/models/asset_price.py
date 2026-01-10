from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Integer
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
    
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    
    # Asset reference
    asset_id: int = Column(Integer, ForeignKey("asset.id"), nullable=False, index=True)
    
    # Price in native currency
    price: float = Column(Float, nullable=False)
    currency: Currency = Column(Enum(Currency), nullable=False)
    
    # Converted prices for portfolio calculations
    price_usd: float = Column(Float, nullable=False)
    price_mxn: float = Column(Float, nullable=False)
    
    # Market data
    open_price: float = Column(Float, nullable=True)
    high_price: float = Column(Float, nullable=True)
    low_price: float = Column(Float, nullable=True)
    previous_close: float = Column(Float, nullable=True)
    volume: float = Column(Float, nullable=True)
    
    # Change metrics
    change: float = Column(Float, nullable=True)  # Absolute change
    change_percent: float = Column(Float, nullable=True)  # Percentage change
    
    # When this price was fetched from the API
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    asset: "Asset" = relationship("Asset", back_populates="prices")

