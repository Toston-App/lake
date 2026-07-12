from datetime import datetime
import math
from typing import Optional

from pydantic import BaseModel, root_validator, validator

from app.models.asset import Currency
from app.models.investment_transaction import TransactionType
from app.schemas.asset import ExternalAssetProvider


# Shared properties
class InvestmentTransactionBase(BaseModel):
    holding_id: Optional[int] = None
    account_id: Optional[int] = None
    transaction_type: Optional[TransactionType] = None
    quantity: Optional[float] = None
    price_per_unit: Optional[float] = None
    currency: Optional[Currency] = Currency.USD
    fees: Optional[float] = 0.0
    exchange_rate_to_usd: Optional[float] = None
    exchange_rate_to_mxn: Optional[float] = None
    notes: Optional[str] = None
    executed_at: Optional[datetime] = None

    @validator("holding_id", "account_id", pre=True)
    def identifiers_must_be_positive(cls, v):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            raise ValueError("Identifiers must be positive integers")
        return v

    @validator("quantity", "price_per_unit", "fees", always=True)
    def round_floats(cls, v):
        if v is not None:
            if not math.isfinite(v):
                raise ValueError("Value must be finite")
            return round(v, 6)
        return v

    @validator("quantity", always=True)
    def quantity_must_be_positive(cls, v):
        if v is not None and (v <= 0 or v > 1e15):
            raise ValueError("Quantity must be between 0 and 1e15")
        return v

    @validator("price_per_unit", always=True)
    def price_must_be_positive(cls, v):
        if v is not None and (v < 0 or v > 1e15):
            raise ValueError("Price per unit must be between 0 and 1e15")
        return v

    @validator("fees")
    def fees_must_be_non_negative(cls, v):
        if v is not None and (v < 0 or v > 1e15):
            raise ValueError("Fees must be between 0 and 1e15")
        return v

    @validator("exchange_rate_to_usd", "exchange_rate_to_mxn")
    def exchange_rate_must_be_positive_and_finite(cls, v):
        if v is not None and (not math.isfinite(v) or v <= 0 or v > 1e6):
            raise ValueError("Exchange rate must be finite and between 0 and 1e6")
        return v

    @validator("notes")
    def limit_notes(cls, v):
        if v is not None and len(v) > 2000:
            raise ValueError("Notes must be at most 2000 characters")
        return v

    class Config:
        extra = "forbid"


# Properties to receive on Transaction creation
class InvestmentTransactionCreate(InvestmentTransactionBase):
    holding_id: int
    account_id: int
    transaction_type: TransactionType
    quantity: float
    price_per_unit: float
    executed_at: datetime

    @validator("executed_at", pre=True)
    def parse_executed_at(cls, v):
        if isinstance(v, str):
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if isinstance(v, datetime) and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("executed_at must include a timezone")
        return v

    @root_validator(skip_on_failure=True)
    def total_amount_must_be_safe(cls, values):
        quantity = values.get("quantity")
        price = values.get("price_per_unit")
        if quantity is not None and price is not None:
            total = quantity * price
            if not math.isfinite(total) or total > 1e30:
                raise ValueError("Transaction total is too large")
        return values


# Properties to receive on Transaction update
class InvestmentTransactionUpdate(InvestmentTransactionBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class InvestmentTransactionInDBBase(InvestmentTransactionBase):
    id: int
    owner_id: int
    account_id: int
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
    # Asset identity (required for selected external assets)
    asset_id: Optional[int] = None
    provider: Optional[ExternalAssetProvider] = None
    external_id: Optional[str] = None

    # Transaction details
    transaction_type: TransactionType
    quantity: float
    price_per_unit: float
    fees: float = 0.0
    executed_at: datetime
    account_id: int
    notes: Optional[str] = None
    
    # Optional exchange rates (auto-fetched if not provided)
    exchange_rate_to_usd: Optional[float] = None
    exchange_rate_to_mxn: Optional[float] = None

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

    @root_validator(skip_on_failure=True)
    def validate_asset_identity(cls, values):
        asset_id = values.get("asset_id")
        provider = values.get("provider")
        external_id = values.get("external_id")

        if asset_id is not None:
            if provider is not None or external_id:
                raise ValueError(
                    "Provide either asset_id or provider+external_id, not both"
                )
            return values

        if provider is not None and external_id:
            return values

        raise ValueError("Provide asset_id or provider+external_id")

    @validator("quantity", "price_per_unit", "fees", always=True)
    def round_floats(cls, v):
        if v is not None:
            if not math.isfinite(v):
                raise ValueError("Value must be finite")
            return round(v, 6)
        return v

    @validator("quantity", always=True)
    def quantity_must_be_positive(cls, v):
        if v is not None and (v <= 0 or v > 1e15):
            raise ValueError("Quantity must be between 0 and 1e15")
        return v

    @validator("price_per_unit", always=True)
    def price_must_be_positive(cls, v):
        if v is not None and (v < 0 or v > 1e15):
            raise ValueError("Price per unit must be between 0 and 1e15")
        return v

    @validator("fees")
    def fees_must_be_non_negative(cls, v):
        if v is not None and (v < 0 or v > 1e15):
            raise ValueError("Fees must be between 0 and 1e15")
        return v

    @validator("exchange_rate_to_usd", "exchange_rate_to_mxn")
    def exchange_rate_must_be_positive_and_finite(cls, v):
        if v is not None and (not math.isfinite(v) or v <= 0 or v > 1e6):
            raise ValueError("Exchange rate must be finite and between 0 and 1e6")
        return v

    @validator("notes")
    def limit_notes(cls, v):
        if v is not None and len(v) > 2000:
            raise ValueError("Notes must be at most 2000 characters")
        return v

    @validator("executed_at", pre=True)
    def parse_executed_at(cls, v):
        if isinstance(v, str):
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if isinstance(v, datetime) and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("executed_at must include a timezone")
        return v

    @root_validator(skip_on_failure=True)
    def total_amount_must_be_safe(cls, values):
        quantity = values.get("quantity")
        price = values.get("price_per_unit")
        if quantity is not None and price is not None:
            total = quantity * price
            if not math.isfinite(total) or total > 1e30:
                raise ValueError("Transaction total is too large")
        return values

    class Config:
        extra = "forbid"


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
