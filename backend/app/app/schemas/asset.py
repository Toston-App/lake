from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, validator

from app.models.asset import AssetClass, AssetType, Currency, Market, ASSET_TYPE_TO_CLASS


# Shared properties
class AssetBase(BaseModel):
    symbol: Optional[str] = None
    name: Optional[str] = None
    asset_class: Optional[AssetClass] = None
    asset_type: Optional[AssetType] = None
    currency: Optional[Currency] = Currency.USD
    market: Optional[Market] = Market.NYSE
    sector: Optional[str] = None
    country: Optional[str] = "US"
    coingecko_id: Optional[str] = None
    is_active: Optional[bool] = True

    @validator("symbol", pre=True, always=True)
    def uppercase_symbol(cls, v):
        if v is not None:
            return v.upper().strip()
        return v

    @validator("asset_class", pre=True, always=True)
    def infer_asset_class(cls, v, values):
        """Infer asset_class from asset_type if not provided."""
        if v is None and "asset_type" in values and values["asset_type"] is not None:
            return ASSET_TYPE_TO_CLASS.get(values["asset_type"])
        return v


# Properties to receive on Asset creation
class AssetCreate(AssetBase):
    symbol: str
    name: str
    asset_type: AssetType

    @validator("asset_class", pre=True, always=True)
    def set_asset_class(cls, v, values):
        """Auto-set asset_class from asset_type."""
        if v is None and "asset_type" in values:
            return ASSET_TYPE_TO_CLASS.get(values["asset_type"])
        return v


# Properties to receive on Asset update
class AssetUpdate(AssetBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class AssetInDBBase(AssetBase):
    id: int
    symbol: str
    name: str
    asset_class: AssetClass
    asset_type: AssetType

    class Config:
        orm_mode = True


# Properties to return to client
class Asset(AssetInDBBase):
    pass


# Properties stored in DB
class AssetInDB(AssetInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# Asset with current price
class AssetWithPrice(Asset):
    current_price: Optional[float] = None
    price_currency: Optional[Currency] = None
    price_change: Optional[float] = None
    price_change_percent: Optional[float] = None
    price_updated_at: Optional[datetime] = None


class AssetDeletionResponse(BaseModel):
    message: str


# External asset search result (from Yahoo Finance, etc.)
class ExternalAssetProvider(str, Enum):
    YAHOO = "yahoo"
    COINGECKO = "coingecko"


class ExternalAssetSearchResult(BaseModel):
    provider: ExternalAssetProvider = ExternalAssetProvider.YAHOO
    external_id: str
    symbol: str
    name: str
    asset_type: AssetType
    market: Market
    currency: Currency
    country: str
    exchange: Optional[str] = None


# External crypto search result (from CoinGecko)
class ExternalCryptoSearchResult(BaseModel):
    provider: ExternalAssetProvider = ExternalAssetProvider.COINGECKO
    external_id: str
    symbol: str
    name: str
    asset_type: AssetType
    market: Market
    currency: Currency
    coingecko_id: str
    market_cap_rank: Optional[int] = None
