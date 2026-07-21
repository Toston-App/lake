from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.asset import (
    ASSET_TYPE_TO_CLASS,
    AssetClass,
    AssetType,
    Currency,
    Market,
)


# Shared properties
class AssetBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, max_length=255)
    asset_class: AssetClass | None = None
    asset_type: AssetType | None = None
    currency: Currency | None = Currency.USD
    market: Market | None = Market.NYSE
    sector: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default="US", max_length=32)
    coingecko_id: str | None = Field(default=None, max_length=128)
    is_active: bool | None = True

    @field_validator("symbol", mode="before")
    @classmethod
    def uppercase_symbol(cls, v):
        if isinstance(v, str):
            return v.upper().strip()
        return v

    @model_validator(mode="before")
    @classmethod
    def infer_asset_class(cls, data: Any) -> Any:
        """Infer asset_class from asset_type when not provided.

        Runs before field validation so both fields are visible regardless
        of declaration order (Pydantic v2 no longer exposes sibling fields
        to per-field validators reliably).
        """
        if not isinstance(data, dict):
            return data
        if data.get("asset_class") is None and data.get("asset_type") is not None:
            inferred = ASSET_TYPE_TO_CLASS.get(data["asset_type"])
            if inferred is not None:
                data["asset_class"] = inferred
        return data


# Properties to receive on Asset creation
class AssetCreate(AssetBase):
    symbol: str
    name: str
    asset_type: AssetType


# Properties to receive on Asset update
class AssetUpdate(AssetBase):
    updated_at: datetime | None = None


# Properties shared by models stored in DB
class AssetInDBBase(AssetBase):
    id: int
    symbol: str
    name: str
    asset_class: AssetClass
    asset_type: AssetType

    model_config = ConfigDict(from_attributes=True, extra="forbid")


# Properties to return to client
class Asset(AssetInDBBase):
    pass


# Properties stored in DB
class AssetInDB(AssetInDBBase):
    created_at: datetime | None = None
    updated_at: datetime | None = None


# Asset with current price
class AssetWithPrice(Asset):
    current_price: float | None = None
    price_currency: Currency | None = None
    price_change: float | None = None
    price_change_percent: float | None = None
    price_updated_at: datetime | None = None


class AssetDeletionResponse(BaseModel):
    message: str


# External asset search result (from Yahoo Finance, etc.)
class ExternalAssetProvider(str, Enum):
    YAHOO = "yahoo"
    COINGECKO = "coingecko"


class ExternalAssetSearchResult(BaseModel):
    provider: ExternalAssetProvider = ExternalAssetProvider.YAHOO
    external_id: str = Field(max_length=128)
    symbol: str = Field(max_length=32)
    name: str = Field(max_length=255)
    asset_type: AssetType
    market: Market
    currency: Currency
    country: str = Field(max_length=32)
    exchange: str | None = Field(default=None, max_length=32)


# External crypto search result (from CoinGecko)
class ExternalCryptoSearchResult(BaseModel):
    provider: ExternalAssetProvider = ExternalAssetProvider.COINGECKO
    external_id: str = Field(max_length=128)
    symbol: str = Field(max_length=32)
    name: str = Field(max_length=255)
    asset_type: AssetType
    market: Market
    currency: Currency
    coingecko_id: str = Field(max_length=128)
    market_cap_rank: int | None = None
