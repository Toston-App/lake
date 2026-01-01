from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class RecapStatus(str, Enum):
    """Status of recap generation"""

    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class RecapStatusResponse(BaseModel):
    """Response model for recap status checks"""

    user_id: int
    year: int
    status: RecapStatus
    message: Optional[str] = None


# Category Analysis Models
class CategoryConsistency(BaseModel):
    """Category spent on most consistently across months"""

    category_id: int
    month_count: int  # Number of months with spending in this category
    total_months: int  # Total months in the year with any spending


class TotalTransactions(BaseModel):
    """Total number of transactions in the year"""

    income_count: int
    expense_count: int
    total_count: int
    average_per_day: float

class SpendingCategoryItem(BaseModel):
    """Single category in top spending list"""

    category_id: int
    total_amount: float
    percentage: float  # Percentage of total spending


class TopSpendingCategories(BaseModel):
    """Top 5 spending categories by total amount"""

    categories: list[SpendingCategoryItem]
    total_spending: float


# Core Analysis Models
class LargestTransaction(BaseModel):
    """Largest single transaction details"""

    id: int
    amount: float
    description: Optional[str] = None
    date: date
    category_id: Optional[int] = None


class LargestTransactions(BaseModel):
    """Largest expense and income transactions"""

    largest_expense: Optional[LargestTransaction] = None
    largest_income: Optional[LargestTransaction] = None


class MonthlyHighlight(BaseModel):
    """Monthly financial highlight"""

    month: int  # 1-12
    month_name: str  # "January", "February", etc.
    amount: float


class MonthlyHighlights(BaseModel):
    """Most expensive month and best savings month"""

    most_expensive_month: Optional[MonthlyHighlight] = None
    most_expensive_total: float = 0.0
    best_savings_month: Optional[MonthlyHighlight] = None
    best_savings_amount: float = 0.0


class NetChange(BaseModel):
    """Balance change from start to end of year"""

    starting_balance: float
    ending_balance: float
    absolute_change: float
    percentage_change: Optional[float] = None  # None if starting_balance is 0


class IncomeVsSpending(BaseModel):
    """Total income vs total expenses for the year"""

    total_income: float
    total_expenses: float
    net_savings: float
    savings_rate: Optional[float] = None  # Percentage of income saved


# Income Analysis Models
class BestEarningMonth(BaseModel):
    """Month with highest total income"""

    month: int
    month_name: str
    total_amount: float


class IncomeSourceItem(BaseModel):
    """Single income source in ranking"""

    subcategory_id: int
    total_amount: float
    percentage: float


class MainIncomeSource(BaseModel):
    """Primary income source by total amount"""

    top_source: Optional[IncomeSourceItem] = None
    total_income: float


class MostDiverseIncomeMonth(BaseModel):
    """Month with most diverse income sources"""

    month: int
    month_name: str
    unique_source_count: int


# Places/Sites Analysis Models
class PlaceItem(BaseModel):
    """Single place in rankings"""

    place_id: int
    place_name: str
    total_amount: float
    transaction_count: int


class TopPlaces(BaseModel):
    """Top 5 places by spending and most visited"""

    top_places_by_spending: list[PlaceItem]
    most_visited_place: Optional[PlaceItem] = None
    total_unique_places: int


# Unified Recap Model
class UserRecap(BaseModel):
    """Complete annual financial recap for a user"""

    user_id: int
    year: int
    generated_at: str  # ISO datetime string

    # Category Analysis
    category_consistency: Optional[CategoryConsistency] = None
    top_spending_categories: Optional[TopSpendingCategories] = None

    # Core Analysis
    total_transactions: Optional[TotalTransactions] = None
    largest_transactions: Optional[LargestTransactions] = None
    monthly_highlights: Optional[MonthlyHighlights] = None
    net_change: Optional[NetChange] = None
    income_vs_spending: Optional[IncomeVsSpending] = None

    # Income Analysis
    best_earning_month: Optional[BestEarningMonth] = None
    main_income_source: Optional[MainIncomeSource] = None
    most_diverse_income_month: Optional[MostDiverseIncomeMonth] = None

    # Places Analysis
    top_places: Optional[TopPlaces] = None
