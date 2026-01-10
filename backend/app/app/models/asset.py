import enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .asset_price import AssetPrice
    from .holding import Holding


class AssetClass(str, enum.Enum):
    """Primary classification of investment assets."""
    EQUITIES = "equities"         # Stocks, ETFs
    FIXED_INCOME = "fixed_income"  # Bonds, CETES, Treasury
    CRYPTO = "crypto"             # Cryptocurrencies
    FUNDS = "funds"               # Mutual funds, Index funds


class AssetType(str, enum.Enum):
    """Specific type within an asset class."""
    # Equities
    STOCK = "stock"
    ETF = "etf"
    # Fixed Income
    BOND = "bond"
    CETES = "cetes"
    TREASURY = "treasury"
    # Crypto
    CRYPTOCURRENCY = "cryptocurrency"
    # Funds
    MUTUAL_FUND = "mutual_fund"
    INDEX_FUND = "index_fund"


class Currency(str, enum.Enum):
    """Supported currencies for assets."""
    MXN = "MXN"  # Mexican Peso
    USD = "USD"  # US Dollar


class Market(str, enum.Enum):
    """Markets where assets are traded."""
    BMV = "BMV"        # Bolsa Mexicana de Valores
    NYSE = "NYSE"      # New York Stock Exchange
    NASDAQ = "NASDAQ"  # NASDAQ
    CRYPTO = "CRYPTO"  # Crypto exchanges
    OTC = "OTC"        # Over the counter (bonds, CETES, mutual funds)


# Mapping of asset types to their parent asset class
ASSET_TYPE_TO_CLASS: dict[AssetType, AssetClass] = {
    AssetType.STOCK: AssetClass.EQUITIES,
    AssetType.ETF: AssetClass.EQUITIES,
    AssetType.BOND: AssetClass.FIXED_INCOME,
    AssetType.CETES: AssetClass.FIXED_INCOME,
    AssetType.TREASURY: AssetClass.FIXED_INCOME,
    AssetType.CRYPTOCURRENCY: AssetClass.CRYPTO,
    AssetType.MUTUAL_FUND: AssetClass.FUNDS,
    AssetType.INDEX_FUND: AssetClass.FUNDS,
}


class Asset(Base):
    """
    Represents a trackable investment asset.
    
    Assets are global entities (not user-specific) that can be referenced
    by multiple users' holdings.
    """
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    symbol: str = Column(String, index=True, nullable=False, unique=True)
    name: str = Column(String, index=True, nullable=False)
    
    # Classification
    asset_class: AssetClass = Column(
        Enum(AssetClass), 
        index=True, 
        nullable=False
    )
    asset_type: AssetType = Column(
        Enum(AssetType), 
        index=True, 
        nullable=False
    )
    
    # Market and currency
    currency: Currency = Column(
        Enum(Currency), 
        index=True, 
        nullable=False, 
        default=Currency.USD
    )
    market: Market = Column(
        Enum(Market), 
        index=True, 
        nullable=False, 
        default=Market.NYSE
    )
    
    # Additional metadata
    sector: str = Column(String, nullable=True)  # e.g., "Technology", "Finance"
    country: str = Column(String, nullable=True, default="US")  # e.g., "US", "MX"
    
    # Status
    is_active: bool = Column(Boolean, default=True, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    holdings: list["Holding"] = relationship("Holding", back_populates="asset")
    prices: list["AssetPrice"] = relationship(
        "AssetPrice", 
        back_populates="asset", 
        cascade="all, delete-orphan"
    )

