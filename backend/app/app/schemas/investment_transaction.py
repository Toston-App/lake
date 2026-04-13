from datetime import datetime
from typing import Optional

from pydantic import BaseModel, validator

from app.models.asset import AssetType, Currency, Market
from app.models.broker import Broker
from app.models.investment_transaction import TransactionType


# Shared properties
class InvestmentTransactionBase(BaseModel):
    holding_id: Optional[int] = None
    transaction_type: Optional[TransactionType] = None
    quantity: Optional[float] = None
    price_per_unit: Optional[float] = None
    currency: Optional[Currency] = Currency.USD
    fees: Optional[float] = 0.0
    exchange_rate_to_usd: Optional[float] = None
    exchange_rate_to_mxn: Optional[float] = None
    notes: Optional[str] = None
    broker: Optional[Broker] = None
    executed_at: Optional[datetime] = None

    @validator("quantity", "price_per_unit", "fees", pre=True, always=True)
    def round_floats(cls, v):
        if v is not None:
            return round(v, 6)
        return v

    @validator("quantity", pre=True, always=True)
    def quantity_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Quantity must be positive")
        return v

    @validator("price_per_unit", pre=True, always=True)
    def price_must_be_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("Price per unit must be non-negative")
        return v


# Properties to receive on Transaction creation
class InvestmentTransactionCreate(InvestmentTransactionBase):
    holding_id: int
    transaction_type: TransactionType
    quantity: float
    price_per_unit: float
    executed_at: datetime

    @validator("executed_at", pre=True)
    def parse_executed_at(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


# Properties to receive on Transaction update
class InvestmentTransactionUpdate(InvestmentTransactionBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class InvestmentTransactionInDBBase(InvestmentTransactionBase):
    id: int
    owner_id: int
    holding_id: int
    transaction_type: TransactionType
    quantity: float
    price_per_unit: float
    total_amount: float
    executed_at: datetime

    class Config:
        orm_mode = True


# Properties to return to client
class InvestmentTransaction(InvestmentTransactionInDBBase):
    pass


# Properties stored in DB
class InvestmentTransactionInDB(InvestmentTransactionInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# Transaction with asset details
class InvestmentTransactionWithAsset(InvestmentTransaction):
    symbol: Optional[str] = None
    asset_name: Optional[str] = None


class InvestmentTransactionDeletionResponse(BaseModel):
    message: str


# Schema for creating a transaction with asset info (auto-creates asset and holding)
class TransactionWithAssetCreate(BaseModel):
    """
    Create a transaction along with the asset and holding if they don't exist.
    
    This allows users to record a transaction without first manually creating
    an asset and holding.
    """
    # Asset info (required for new assets, used to find/create the asset)
    symbol: str
    asset_name: Optional[str] = None  # Optional - can be auto-filled or manual
    asset_type: AssetType = AssetType.STOCK
    market: Market = Market.NYSE
    currency: Currency = Currency.USD
    country: str = "US"
    sector: Optional[str] = None
    coingecko_id: Optional[str] = None
    
    # Transaction details
    transaction_type: TransactionType
    quantity: float
    price_per_unit: float
    fees: float = 0.0
    executed_at: datetime
    broker: Optional[Broker] = None
    notes: Optional[str] = None
    
    # Optional exchange rates (auto-fetched if not provided)
    exchange_rate_to_usd: Optional[float] = None
    exchange_rate_to_mxn: Optional[float] = None

    @validator("symbol", pre=True, always=True)
    def uppercase_symbol(cls, v):
        if v is not None:
            return v.upper().strip()
        return v

    @validator("quantity", "price_per_unit", "fees", pre=True, always=True)
    def round_floats(cls, v):
        if v is not None:
            return round(v, 6)
        return v

    @validator("quantity", pre=True, always=True)
    def quantity_must_be_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Quantity must be positive")
        return v

    @validator("price_per_unit", pre=True, always=True)
    def price_must_be_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("Price per unit must be non-negative")
        return v

    @validator("executed_at", pre=True)
    def parse_executed_at(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class TransactionWithAssetResponse(BaseModel):
    """Response after creating a transaction with asset info."""
    transaction: "InvestmentTransaction"
    asset_created: bool
    holding_created: bool
    asset_id: int
    holding_id: int

    class Config:
        orm_mode = True


# Update forward references
TransactionWithAssetResponse.update_forward_refs()
