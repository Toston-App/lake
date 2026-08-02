from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, root_validator, validator

from app.models.asset import Currency


# Shared properties
class AssetPriceBase(BaseModel):
    asset_id: int | None = None
    price: Decimal | None = None
    currency: Currency | None = None
    price_usd: Decimal | None = None
    price_mxn: Decimal | None = None
    open_price: Decimal | None = None
    high_price: Decimal | None = None
    low_price: Decimal | None = None
    previous_close: Decimal | None = None
    volume: Decimal | None = None
    change: Decimal | None = None
    change_percent: Decimal | None = None

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
            not value.is_finite() or value < 0 or value > Decimal("1e15")
        ):
            raise ValueError("Price must be finite and between 0 and 1e15")
        return value

    @validator("volume")
    def validate_volume(cls, value):
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("Volume must be finite and non-negative")
        return value

    @validator("change", "change_percent")
    def validate_change(cls, value):
        if value is not None and not value.is_finite():
            raise ValueError("Change values must be finite")
        return value

    class Config:
        extra = "forbid"


# Properties to receive on AssetPrice creation
class AssetPriceCreate(AssetPriceBase):
    asset_id: int
    price: Decimal
    currency: Currency
    price_usd: Decimal
    price_mxn: Decimal

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
    price: Decimal
    currency: Currency
    price_usd: Decimal
    price_mxn: Decimal
    fetched_at: datetime

    class Config:
        from_attributes = True


# Properties to return to client
class AssetPrice(AssetPriceInDBBase):
    pass


# Properties stored in DB
class AssetPriceInDB(AssetPriceInDBBase):
    created_at: datetime | None = None


# Current price response (simplified)
class CurrentPrice(BaseModel):
    symbol: str
    price: Decimal
    currency: Currency
    price_usd: Decimal
    price_mxn: Decimal
    change: Decimal | None = None
    change_percent: Decimal | None = None
    fetched_at: datetime


# Price refresh response
class PriceRefreshResponse(BaseModel):
    message: str
    updated_count: int
    failed_symbols: list[str] = []
