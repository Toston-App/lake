from datetime import datetime
from typing import Optional

from pydantic import BaseModel

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


# Properties to receive on AssetPrice creation
class AssetPriceCreate(AssetPriceBase):
    asset_id: int
    price: float
    currency: Currency
    price_usd: float
    price_mxn: float


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

