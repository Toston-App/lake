from datetime import datetime
from typing import Optional

from pydantic import BaseModel, root_validator, validator

from app.models.asset import AssetClass, AssetType, Currency, Market
from app.schemas.asset import ExternalAssetProvider


# Shared properties
class HoldingBase(BaseModel):
    asset_id: Optional[int] = None
    account_id: Optional[int] = None
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
    asset_id: Optional[int] = None
    account_id: int
    provider: Optional[ExternalAssetProvider] = None
    external_id: Optional[str] = None
    quantity: float
    avg_cost_basis: float
    cost_currency: Currency = Currency.USD

    @validator("external_id", pre=True, always=True)
    def normalize_external_id(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v

    @root_validator(pre=True)
    def validate_asset_source(cls, values):
        asset_id = values.get("asset_id")
        if asset_id is not None:
            return values

        required_external_fields = ("provider", "external_id")
        missing = [field for field in required_external_fields if values.get(field) in (None, "")]
        if missing:
            missing_fields = ", ".join(missing)
            raise ValueError(
                f"Either asset_id must be provided, or external asset identifier is required: {missing_fields}"
            )

        return values


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
    account_id: int
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
