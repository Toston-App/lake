from datetime import datetime

from pydantic import BaseModel

from app.models.asset import AssetClass, AssetType


# Portfolio Summary
class PortfolioSummary(BaseModel):
    """Overall portfolio summary with total values and performance."""

    total_value_usd: float
    total_value_mxn: float

    # Total invested by original currency
    total_invested_usd: float  # Sum of investments made in USD
    total_invested_mxn: float  # Sum of investments made in MXN

    # Combined total invested (converted to both currencies)
    total_invested_combined_usd: float  # All investments converted to USD
    total_invested_combined_mxn: float  # All investments converted to MXN

    # Overall performance
    total_gain_loss: float
    total_gain_loss_pct: float

    # Day change
    day_change: float | None = None
    day_change_pct: float | None = None

    # Counts
    total_holdings: int
    total_assets: int

    # Last updated
    last_updated: datetime


# Allocation breakdown item
class AllocationItem(BaseModel):
    """Single item in an allocation breakdown."""

    name: str
    value: str  # The enum value or identifier
    color: str | None = "#168FFF"  # Default color if not provided
    total_value_usd: float
    total_value_mxn: float
    percentage: float
    holdings_count: int


# Allocation by Asset Class
class AllocationByClass(BaseModel):
    """Portfolio allocation broken down by asset class."""

    total_value_usd: float
    total_value_mxn: float
    allocations: list[AllocationItem]

    # Breakdown
    equities: AllocationItem | None = None
    fixed_income: AllocationItem | None = None
    crypto: AllocationItem | None = None
    funds: AllocationItem | None = None


# Allocation by Currency
class AllocationByCurrency(BaseModel):
    """Portfolio allocation broken down by currency exposure."""

    total_value_usd: float
    total_value_mxn: float
    allocations: list[AllocationItem]

    # Breakdown
    usd_exposure: AllocationItem | None = None
    mxn_exposure: AllocationItem | None = None


# Allocation by Market
class AllocationByMarket(BaseModel):
    """Portfolio allocation broken down by market."""

    total_value_usd: float
    total_value_mxn: float
    allocations: list[AllocationItem]


# Allocation by Asset Type
class AllocationByType(BaseModel):
    """Portfolio allocation broken down by specific asset type."""

    total_value_usd: float
    total_value_mxn: float
    allocations: list[AllocationItem]


# Allocation by Country
class AllocationByCountry(BaseModel):
    """Portfolio allocation broken down by country."""

    total_value_usd: float
    total_value_mxn: float
    allocations: list[AllocationItem]


# Allocation by Account
class AllocationByAccount(BaseModel):
    """Portfolio allocation broken down by account."""

    total_value_usd: float
    total_value_mxn: float
    allocations: list[AllocationItem]


# Performance over time
class PerformanceDataPoint(BaseModel):
    """Single data point in performance history."""

    date: datetime
    value_usd: float
    value_mxn: float
    gain_loss: float
    gain_loss_pct: float


class PortfolioPerformance(BaseModel):
    """Portfolio performance over time."""

    period: str  # "1D", "1W", "1M", "3M", "1Y", "ALL"
    start_value: float
    end_value: float
    absolute_return: float
    percentage_return: float
    data_points: list[PerformanceDataPoint]


# Top holdings
class TopHolding(BaseModel):
    """Holding summary for top holdings list."""

    symbol: str
    name: str
    asset_class: AssetClass
    asset_type: AssetType
    quantity: float
    current_value_usd: float
    current_value_mxn: float
    percentage_of_portfolio: float
    gain_loss: float
    gain_loss_pct: float


class TopHoldingsResponse(BaseModel):
    """Response with top holdings by value."""

    holdings: list[TopHolding]
    total_shown: int
    total_holdings: int
