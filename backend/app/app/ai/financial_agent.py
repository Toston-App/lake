from typing import List, Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent, tool
from pydantic_ai.models.openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.financial_analytics import FinancialAnalytics, SpendingAnalysis, CategoryAnalysis, TrendAnalysis, AccountBalance
from app.core.config import settings


class FinancialAgent(Agent):
    """Financial analytics agent for handling user queries about spending, categories, trends, and account balances"""
    
    def __init__(self, db: AsyncSession, user_id: int):
        super().__init__(
            model=OpenAI(
                model="gpt-4o-mini",
                api_key=settings.OPENAI_API_KEY,
                temperature=0.1
            ),
            system_prompt="""You are a helpful financial assistant that can analyze spending patterns, 
            track categories, analyze trends, and provide account balance information. 
            
            You can help users with:
            - Spending analysis for different time periods
            - Category-specific spending analysis
            - Trend analysis over time
            - Account balance information
            
            Always provide clear, actionable insights and format monetary amounts appropriately.
            Be conversational and helpful in your responses."""
        )
        self.analytics = FinancialAnalytics(db, user_id)
    
    @tool
    async def analyze_spending(
        self, 
        period: str = Field(description="Time period to analyze (e.g., 'this month', 'last week', 'this year')")
    ) -> SpendingAnalysis:
        """Analyze spending for a specific time period"""
        return await self.analytics.get_spending_analysis(period=period)
    
    @tool
    async def analyze_category_spending(
        self, 
        category_name: str = Field(description="Name of the category to analyze"),
        period: str = Field(description="Time period to analyze (e.g., 'this month', 'last week')")
    ) -> CategoryAnalysis:
        """Analyze spending for a specific category over a time period"""
        return await self.analytics.get_category_analysis(category_name=category_name, period=period)
    
    @tool
    async def analyze_spending_trends(
        self, 
        months: int = Field(description="Number of months to analyze trends for", default=3)
    ) -> TrendAnalysis:
        """Analyze spending trends over the last N months"""
        return await self.analytics.get_trend_analysis(months=months)
    
    @tool
    async def get_all_account_balances(self) -> List[AccountBalance]:
        """Get current balances for all accounts"""
        return await self.analytics.get_account_balances()
    
    @tool
    async def get_specific_account_balance(
        self, 
        account_name: str = Field(description="Name of the specific account to get balance for")
    ) -> Optional[AccountBalance]:
        """Get balance for a specific account"""
        return await self.analytics.get_specific_account_balance(account_name)
    
    async def chat(self, message: str) -> str:
        """Process a user message and return a helpful response"""
        try:
            result = await self.run(message)
            return result.content
        except Exception as e:
            return f"I apologize, but I encountered an error while processing your request: {str(e)}. Please try rephrasing your question."


class ChatMessage(BaseModel):
    """Chat message model for the API"""
    message: str = Field(description="User's message")
    user_id: int = Field(description="User ID")


class ChatResponse(BaseModel):
    """Chat response model for the API"""
    response: str = Field(description="Agent's response")
    success: bool = Field(description="Whether the request was successful")
    error: Optional[str] = Field(description="Error message if any", default=None) 