from datetime import datetime
from typing import Optional

from pydantic import BaseModel, validator

from app.models.asset import AssetClass, AssetType, Currency, Market


# Shared properties
class HoldingBase(BaseModel):
    asset_id: Optional[int] = None
    quantity: Optional[float] = 0.0
    avg_cost_basis: Optional[float] = 0.0
    cost_currency: Optional[Currency] = Currency.USD
    total_invested: Optional[float] = 0.0

    @validator("quantity", "avg_cost_basis", "total_invested", pre=True, always=True)
    def round_floats(cls, v):
        if v is not None:
            return round(v, 6)  # Support fractional shares/crypto
        return v


# Properties to receive on Holding creation
class HoldingCreate(HoldingBase):
    asset_id: int
    quantity: float
    avg_cost_basis: float
    cost_currency: Currency = Currency.USD


# Properties to receive on Holding update
class HoldingUpdate(HoldingBase):
    current_value: Optional[float] = None
    current_value_mxn: Optional[float] = None
    current_value_usd: Optional[float] = None
    unrealized_gain_loss: Optional[float] = None
    unrealized_gain_loss_pct: Optional[float] = None
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class HoldingInDBBase(HoldingBase):
    id: int
    owner_id: int
    asset_id: int
    quantity: float
    current_value: float
    current_value_mxn: float
    current_value_usd: float
    unrealized_gain_loss: float
    unrealized_gain_loss_pct: float

    class Config:
        orm_mode = True


# Properties to return to client
class Holding(HoldingInDBBase):
    pass


# Properties stored in DB
class HoldingInDB(HoldingInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# Holding with asset details (for rich responses)
class HoldingWithAsset(Holding):
    # Asset details
    symbol: Optional[str] = None
    asset_name: Optional[str] = None
    asset_class: Optional[AssetClass] = None
    asset_type: Optional[AssetType] = None
    asset_currency: Optional[Currency] = None
    market: Optional[Market] = None
    sector: Optional[str] = None
    country: Optional[str] = None
    
    # Current price
    current_price: Optional[float] = None
    price_change: Optional[float] = None
    price_change_percent: Optional[float] = None


class HoldingDeletionResponse(BaseModel):
    message: str

