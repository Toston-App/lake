from datetime import datetime
import math
from typing import Optional

from pydantic import BaseModel, root_validator, validator

from app.models.asset import Currency


# Shared properties
class AssetPriceBase(BaseModel):
    asset_id: Optional[int] = None
    price: Optional[float] = None
    currency: Optional[Currency] = None
    price_usd: Optional[float] = None
    price_mxn: Optional[float] = None
    open_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    previous_close: Optional[float] = None
    volume: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None

    @validator(
        "price",
        "price_usd",
        "price_mxn",
        "open_price",
        "high_price",
        "low_price",
        "previous_close",
    )
    def validate_price(cls, value):
        if value is not None and (
            not math.isfinite(value) or value < 0 or value > 1e15
        ):
            raise ValueError("Price must be finite and between 0 and 1e15")
        return value

    @validator("volume")
    def validate_volume(cls, value):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("Volume must be finite and non-negative")
        return value

    @validator("change", "change_percent")
    def validate_change(cls, value):
        if value is not None and not math.isfinite(value):
            raise ValueError("Change values must be finite")
        return value

    class Config:
        extra = "forbid"


# Properties to receive on AssetPrice creation
class AssetPriceCreate(AssetPriceBase):
    asset_id: int
    price: float
    currency: Currency
    price_usd: float
    price_mxn: float

    @root_validator(skip_on_failure=True)
    def required_prices_must_be_positive(cls, values):
        for field in ("price", "price_usd", "price_mxn"):
            if values.get(field) is not None and values[field] <= 0:
                raise ValueError(f"{field} must be positive")
        return values


# Properties to receive on AssetPrice update
class AssetPriceUpdate(AssetPriceBase):
    pass


# Properties shared by models stored in DB
class AssetPriceInDBBase(AssetPriceBase):
    id: int
    asset_id: int
    price: float
    currency: Currency
    price_usd: float
    price_mxn: float
    fetched_at: datetime

    class Config:
        orm_mode = True


# Properties to return to client
class AssetPrice(AssetPriceInDBBase):
    pass


# Properties stored in DB
class AssetPriceInDB(AssetPriceInDBBase):
    created_at: Optional[datetime] = None


# Current price response (simplified)
class CurrentPrice(BaseModel):
    symbol: str
    price: float
    currency: Currency
    price_usd: float
    price_mxn: float
    change: Optional[float] = None
    change_percent: Optional[float] = None
    fetched_at: datetime


# Price refresh response
class PriceRefreshResponse(BaseModel):
    message: str
    updated_count: int
    failed_symbols: list[str] = []
