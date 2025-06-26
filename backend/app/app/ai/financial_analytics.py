from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, and_, desc, asc
from sqlalchemy.sql import select

from app import crud, models
from app.crud.crud_expense import expense
from app.crud.crud_account import account


class SpendingAnalysis(BaseModel):
    """Analysis of spending data for a given period"""
    total_spent: float = Field(description="Total amount spent in the period")
    transaction_count: int = Field(description="Number of transactions")
    average_transaction: float = Field(description="Average transaction amount")
    top_categories: List[Dict[str, Any]] = Field(description="Top spending categories")
    top_places: List[Dict[str, Any]] = Field(description="Top spending places")
    daily_breakdown: List[Dict[str, Any]] = Field(description="Daily spending breakdown")


class CategoryAnalysis(BaseModel):
    """Analysis of spending by category"""
    category_name: str = Field(description="Name of the category")
    total_spent: float = Field(description="Total amount spent in this category")
    transaction_count: int = Field(description="Number of transactions")
    percentage_of_total: float = Field(description="Percentage of total spending")
    average_transaction: float = Field(description="Average transaction amount")


class TrendAnalysis(BaseModel):
    """Analysis of spending trends over time"""
    period: str = Field(description="Analysis period (e.g., 'last 3 months')")
    total_spent: float = Field(description="Total amount spent in the period")
    monthly_averages: List[Dict[str, Any]] = Field(description="Monthly spending averages")
    trend_direction: str = Field(description="Trend direction (increasing, decreasing, stable)")
    top_growing_categories: List[Dict[str, Any]] = Field(description="Categories with increasing spending")
    top_declining_categories: List[Dict[str, Any]] = Field(description="Categories with decreasing spending")


class AccountBalance(BaseModel):
    """Account balance information"""
    account_name: str = Field(description="Name of the account")
    account_type: str = Field(description="Type of account")
    current_balance: float = Field(description="Current balance")
    total_expenses: float = Field(description="Total expenses from this account")
    total_incomes: float = Field(description="Total incomes to this account")


class FinancialAnalytics:
    """Financial analytics tools for the chatbot"""
    
    def __init__(self, db: AsyncSession, user_id: int):
        self.db = db
        self.user_id = user_id
    
    async def get_spending_analysis(
        self, 
        start_date: Optional[datetime] = None, 
        end_date: Optional[datetime] = None,
        period: str = "this month"
    ) -> SpendingAnalysis:
        """Analyze spending for a given period"""
        
        # Set default dates if not provided
        if not start_date or not end_date:
            start_date, end_date = self._get_period_dates(period)
        
        # Get expenses for the period
        expenses = await expense.get_multi_by_date(
            db=self.db,
            owner_id=self.user_id,
            start_date=start_date.date(),
            end_date=end_date.strftime("%Y-%m-%d")
        )
        
        if not expenses:
            return SpendingAnalysis(
                total_spent=0.0,
                transaction_count=0,
                average_transaction=0.0,
                top_categories=[],
                top_places=[],
                daily_breakdown=[]
            )
        
        # Calculate basic metrics
        total_spent = sum(exp.amount for exp in expenses)
        transaction_count = len(expenses)
        average_transaction = total_spent / transaction_count if transaction_count > 0 else 0
        
        # Get top categories
        category_totals = {}
        for exp in expenses:
            if exp.category:
                cat_name = exp.category.name
                category_totals[cat_name] = category_totals.get(cat_name, 0) + exp.amount
        
        top_categories = [
            {"category": cat, "amount": amount, "percentage": (amount / total_spent) * 100}
            for cat, amount in sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
        
        # Get top places
        place_totals = {}
        for exp in expenses:
            if exp.place:
                place_name = exp.place.name
                place_totals[place_name] = place_totals.get(place_name, 0) + exp.amount
        
        top_places = [
            {"place": place, "amount": amount, "percentage": (amount / total_spent) * 100}
            for place, amount in sorted(place_totals.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
        
        # Get daily breakdown
        daily_totals = {}
        for exp in expenses:
            date_str = exp.date.strftime("%Y-%m-%d")
            daily_totals[date_str] = daily_totals.get(date_str, 0) + exp.amount
        
        daily_breakdown = [
            {"date": date, "amount": amount}
            for date, amount in sorted(daily_totals.items())
        ]
        
        return SpendingAnalysis(
            total_spent=total_spent,
            transaction_count=transaction_count,
            average_transaction=average_transaction,
            top_categories=top_categories,
            top_places=top_places,
            daily_breakdown=daily_breakdown
        )
    
    async def get_category_analysis(
        self, 
        category_name: str, 
        start_date: Optional[datetime] = None, 
        end_date: Optional[datetime] = None,
        period: str = "this month"
    ) -> CategoryAnalysis:
        """Analyze spending for a specific category"""
        
        # Set default dates if not provided
        if not start_date or not end_date:
            start_date, end_date = self._get_period_dates(period)
        
        # Get all expenses for the period to calculate total
        all_expenses = await expense.get_multi_by_date(
            db=self.db,
            owner_id=self.user_id,
            start_date=start_date.date(),
            end_date=end_date.strftime("%Y-%m-%d")
        )
        
        total_period_spending = sum(exp.amount for exp in all_expenses)
        
        # Get category expenses
        category_expenses = [
            exp for exp in all_expenses 
            if exp.category and exp.category.name.lower() == category_name.lower()
        ]
        
        if not category_expenses:
            return CategoryAnalysis(
                category_name=category_name,
                total_spent=0.0,
                transaction_count=0,
                percentage_of_total=0.0,
                average_transaction=0.0
            )
        
        category_total = sum(exp.amount for exp in category_expenses)
        transaction_count = len(category_expenses)
        average_transaction = category_total / transaction_count if transaction_count > 0 else 0
        percentage_of_total = (category_total / total_period_spending) * 100 if total_period_spending > 0 else 0
        
        return CategoryAnalysis(
            category_name=category_name,
            total_spent=category_total,
            transaction_count=transaction_count,
            percentage_of_total=percentage_of_total,
            average_transaction=average_transaction
        )
    
    async def get_trend_analysis(self, months: int = 3) -> TrendAnalysis:
        """Analyze spending trends over the last N months"""
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=months * 30)
        
        # Get expenses for the period
        expenses = await expense.get_multi_by_date(
            db=self.db,
            owner_id=self.user_id,
            start_date=start_date.date(),
            end_date=end_date.strftime("%Y-%m-%d")
        )
        
        if not expenses:
            return TrendAnalysis(
                period=f"last {months} months",
                total_spent=0.0,
                monthly_averages=[],
                trend_direction="no data",
                top_growing_categories=[],
                top_declining_categories=[]
            )
        
        total_spent = sum(exp.amount for exp in expenses)
        
        # Calculate monthly averages
        monthly_totals = {}
        for exp in expenses:
            month_key = exp.date.strftime("%Y-%m")
            monthly_totals[month_key] = monthly_totals.get(month_key, 0) + exp.amount
        
        monthly_averages = [
            {"month": month, "total": total}
            for month, total in sorted(monthly_totals.items())
        ]
        
        # Determine trend direction
        if len(monthly_averages) >= 2:
            recent_months = monthly_averages[-2:]
            if recent_months[1]["total"] > recent_months[0]["total"]:
                trend_direction = "increasing"
            elif recent_months[1]["total"] < recent_months[0]["total"]:
                trend_direction = "decreasing"
            else:
                trend_direction = "stable"
        else:
            trend_direction = "insufficient data"
        
        # Analyze category trends (simplified)
        category_totals = {}
        for exp in expenses:
            if exp.category:
                cat_name = exp.category.name
                category_totals[cat_name] = category_totals.get(cat_name, 0) + exp.amount
        
        top_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:5]
        top_growing_categories = [{"category": cat, "amount": amount} for cat, amount in top_categories[:3]]
        top_declining_categories = [{"category": cat, "amount": amount} for cat, amount in top_categories[-3:]]
        
        return TrendAnalysis(
            period=f"last {months} months",
            total_spent=total_spent,
            monthly_averages=monthly_averages,
            trend_direction=trend_direction,
            top_growing_categories=top_growing_categories,
            top_declining_categories=top_declining_categories
        )
    
    async def get_account_balances(self) -> List[AccountBalance]:
        """Get current balances for all accounts"""
        
        accounts = await account.get_multi_by_owner(
            db=self.db,
            owner_id=self.user_id
        )
        
        return [
            AccountBalance(
                account_name=acc.name,
                account_type=acc.type.value,
                current_balance=acc.current_balance,
                total_expenses=acc.total_expenses,
                total_incomes=acc.total_incomes
            )
            for acc in accounts
        ]
    
    async def get_specific_account_balance(self, account_name: str) -> Optional[AccountBalance]:
        """Get balance for a specific account"""
        
        accounts = await account.get_multi_by_owner(
            db=self.db,
            owner_id=self.user_id
        )
        
        for acc in accounts:
            if acc.name.lower() == account_name.lower():
                return AccountBalance(
                    account_name=acc.name,
                    account_type=acc.type.value,
                    current_balance=acc.current_balance,
                    total_expenses=acc.total_expenses,
                    total_incomes=acc.total_incomes
                )
        
        return None
    
    def _get_period_dates(self, period: str) -> tuple[datetime, datetime]:
        """Convert period string to start and end dates"""
        now = datetime.now()
        
        if period == "this month":
            start_date = now.replace(day=1)
            end_date = now
        elif period == "last month":
            if now.month == 1:
                start_date = now.replace(year=now.year-1, month=12, day=1)
            else:
                start_date = now.replace(month=now.month-1, day=1)
            end_date = start_date.replace(day=28) + timedelta(days=4)
            end_date = end_date.replace(day=1) - timedelta(days=1)
        elif period == "this week":
            start_date = now - timedelta(days=now.weekday())
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now
        elif period == "last week":
            start_date = now - timedelta(days=now.weekday() + 7)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=6)
        elif period == "this year":
            start_date = now.replace(month=1, day=1)
            end_date = now
        else:
            # Default to this month
            start_date = now.replace(day=1)
            end_date = now
        
        return start_date, end_date 