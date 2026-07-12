from datetime import datetime
import math
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

    @validator("quantity", "avg_cost_basis", "total_invested", always=True)
    def round_floats(cls, v):
        if v is not None:
            if not math.isfinite(v):
                raise ValueError("Value must be finite")
            return round(v, 6)  # Support fractional shares/crypto
        return v

    @validator("quantity", "avg_cost_basis")
    def values_must_be_non_negative(cls, v):
        if v is not None and (v < 0 or v > 1e15):
            raise ValueError("Value must be between 0 and 1e15")
        return v

    class Config:
        extra = "forbid"


# Properties to receive on Holding creation
class HoldingCreate(HoldingBase):
    asset_id: Optional[int] = None
    account_id: int
    provider: Optional[ExternalAssetProvider] = None
    external_id: Optional[str] = None
    quantity: float
    avg_cost_basis: float
    cost_currency: Currency = Currency.USD

    @validator("asset_id", "account_id", pre=True)
    def identifiers_must_be_positive(cls, v):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            raise ValueError("Identifiers must be positive integers")
        return v

    @validator("external_id", pre=True, always=True)
    def normalize_external_id(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if len(v) > 128:
                raise ValueError("External ID must be at most 128 characters")
            return v
        return v

    @root_validator(pre=True)
    def validate_asset_source(cls, values):
        asset_id = values.get("asset_id")
        provider = values.get("provider")
        external_id = values.get("external_id")
        if asset_id is not None:
            if provider is not None or external_id not in (None, ""):
                raise ValueError(
                    "Provide either asset_id or provider+external_id, not both"
                )
            return values

        required_external_fields = ("provider", "external_id")
        missing = [field for field in required_external_fields if values.get(field) in (None, "")]
        if missing:
            missing_fields = ", ".join(missing)
            raise ValueError(
                f"Either asset_id must be provided, or external asset identifier is required: {missing_fields}"
            )

        return values

    @root_validator(skip_on_failure=True)
    def total_invested_must_be_safe(cls, values):
        quantity = values.get("quantity")
        cost = values.get("avg_cost_basis")
        if quantity is not None and cost is not None:
            total = quantity * cost
            if not math.isfinite(total) or total > 1e30:
                raise ValueError("Initial invested total is too large")
        return values


# Properties to receive on Holding update
class HoldingUpdate(BaseModel):
    """User-editable holding fields; valuations and totals are server-owned."""

    quantity: Optional[float] = None
    avg_cost_basis: Optional[float] = None
    cost_currency: Optional[Currency] = None

    @validator("quantity", "avg_cost_basis")
    def validate_financial_value(cls, v):
        if v is not None and (not math.isfinite(v) or v < 0 or v > 1e15):
            raise ValueError("Value must be finite and between 0 and 1e15")
        return round(v, 6) if v is not None else v

    class Config:
        extra = "forbid"


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
