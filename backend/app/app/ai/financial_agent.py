from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from sqlalchemy.ext.asyncio import AsyncSession
from app.ai.financial_analytics import FinancialAnalytics, SpendingAnalysis, CategoryAnalysis, TrendAnalysis, AccountBalance
from app.ai.transaction_parser import TransactionParser, ParsedTransaction
from app.core.config import settings


class FinancialAgentContext:
    """Context class to hold database session and user information for the financial agent"""
    
    def __init__(self, db: AsyncSession, user_id: int):
        self.db = db
        self.user_id = user_id
        self.analytics = FinancialAnalytics(db, user_id)
        self.transaction_parser = TransactionParser(db, user_id)


# Create the agent instance
financial_agent = Agent(
    OpenAIModel('gpt-4o-mini', provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY)),
    # OpenAIResponsesModel('gpt-4o-mini', provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY)),
    deps_type=FinancialAgentContext,
    system_prompt="""You are a helpful financial assistant that can analyze spending patterns, 
    track categories, analyze trends, provide account balance information, and help manage transactions.
    
    You can help users with:
    - Spending analysis for different time periods
    - Category-specific spending analysis
    - Trend analysis over time
    - Account balance information
    - Quick expense entry: "Add $25 for lunch at McDonald's"
    - Income recording: "Add $2000 salary deposit"
    - Transfer tracking: "Transfer $500 from checking to savings"
    
    For transaction management:
    - When users want to add expenses, income, or transfers, use the appropriate tools
    - Confirm the transaction details before creating them
    - Provide clear feedback about what was created
    
    Always provide clear, actionable insights and format monetary amounts appropriately.
    Be conversational and helpful in your responses."""
)


@financial_agent.tool
async def analyze_spending(
    ctx: RunContext[FinancialAgentContext],
    period: str = Field(description="Time period to analyze (e.g., 'this month', 'last week', 'this year')")
) -> SpendingAnalysis:
    """Analyze spending for a specific time period"""
    return await ctx.deps.analytics.get_spending_analysis(period=period)


@financial_agent.tool
async def analyze_category_spending(
    ctx: RunContext[FinancialAgentContext],
    category_name: str = Field(description="Name of the category to analyze"),
    period: str = Field(description="Time period to analyze (e.g., 'this month', 'last week')")
) -> CategoryAnalysis:
    """Analyze spending for a specific category over a time period"""
    return await ctx.deps.analytics.get_category_analysis(category_name=category_name, period=period)


@financial_agent.tool
async def analyze_spending_trends(
    ctx: RunContext[FinancialAgentContext],
    months: int = Field(description="Number of months to analyze trends for", default=3)
) -> TrendAnalysis:
    """Analyze spending trends over the last N months"""
    return await ctx.deps.analytics.get_trend_analysis(months=months)


@financial_agent.tool
async def get_all_account_balances(ctx: RunContext[FinancialAgentContext]) -> List[AccountBalance]:
    """Get current balances for all accounts"""
    return await ctx.deps.analytics.get_account_balances()


@financial_agent.tool
async def get_specific_account_balance(
    ctx: RunContext[FinancialAgentContext],
    account_name: str = Field(description="Name of the specific account to get balance for")
) -> Optional[AccountBalance]:
    """Get balance for a specific account"""
    return await ctx.deps.analytics.get_specific_account_balance(account_name)


@financial_agent.tool
async def create_expense(
    ctx: RunContext[FinancialAgentContext],
    message: str = Field(description="Natural language description of the expense (e.g., 'Add $25 for lunch at McDonald's')")
) -> Dict[str, Any]:
    """Create an expense transaction from natural language input"""
    try:
        parsed_transaction = await ctx.deps.transaction_parser.parse_transaction(message)
        
        if parsed_transaction.transaction_type != "expense":
            return {
                "success": False,
                "error": f"Expected expense transaction, but detected {parsed_transaction.transaction_type}"
            }
        
        result = await ctx.deps.transaction_parser.create_transaction(parsed_transaction)
        
        return {
            "success": True,
            "message": f"✅ Expense recorded: ${result['amount']:.2f} for {result['description']}",
            "transaction": result
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to create expense: {str(e)}"
        }


@financial_agent.tool
async def create_income(
    ctx: RunContext[FinancialAgentContext],
    message: str = Field(description="Natural language description of the income (e.g., 'Add $2000 salary deposit')")
) -> Dict[str, Any]:
    """Create an income transaction from natural language input"""
    try:
        parsed_transaction = await ctx.deps.transaction_parser.parse_transaction(message)
        
        if parsed_transaction.transaction_type != "income":
            return {
                "success": False,
                "error": f"Expected income transaction, but detected {parsed_transaction.transaction_type}"
            }
        
        result = await ctx.deps.transaction_parser.create_transaction(parsed_transaction)
        
        return {
            "success": True,
            "message": f"✅ Income recorded: ${result['amount']:.2f} for {result['description']}",
            "transaction": result
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to create income: {str(e)}"
        }


@financial_agent.tool
async def create_transfer(
    ctx: RunContext[FinancialAgentContext],
    message: str = Field(description="Natural language description of the transfer (e.g., 'Transfer $500 from checking to savings')")
) -> Dict[str, Any]:
    """Create a transfer transaction from natural language input"""
    try:
        parsed_transaction = await ctx.deps.transaction_parser.parse_transaction(message)
        
        if parsed_transaction.transaction_type != "transfer":
            return {
                "success": False,
                "error": f"Expected transfer transaction, but detected {parsed_transaction.transaction_type}"
            }
        
        result = await ctx.deps.transaction_parser.create_transaction(parsed_transaction)
        
        return {
            "success": True,
            "message": f"✅ Transfer recorded: ${result['amount']:.2f} from {result['from_account']} to {result['to_account']}",
            "transaction": result
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to create transfer: {str(e)}"
        }


@financial_agent.tool
async def create_transaction_from_message(
    ctx: RunContext[FinancialAgentContext],
    message: str = Field(description="Natural language description of any transaction (expense, income, or transfer)")
) -> Dict[str, Any]:
    """Create any type of transaction (expense, income, or transfer) from natural language input"""
    try:
        parsed_transaction = await ctx.deps.transaction_parser.parse_transaction(message)
        result = await ctx.deps.transaction_parser.create_transaction(parsed_transaction)
        
        if parsed_transaction.transaction_type == "expense":
            message_text = f"✅ Expense recorded: ${result['amount']:.2f} for {result['description']}"
        elif parsed_transaction.transaction_type == "income":
            message_text = f"✅ Income recorded: ${result['amount']:.2f} for {result['description']}"
        elif parsed_transaction.transaction_type == "transfer":
            message_text = f"✅ Transfer recorded: ${result['amount']:.2f} from {result['from_account']} to {result['to_account']}"
        else:
            message_text = f"✅ Transaction recorded: ${result['amount']:.2f} for {result['description']}"
        
        return {
            "success": True,
            "message": message_text,
            "transaction": result
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to create transaction: {str(e)}"
        }


class FinancialAgent:
    """Wrapper class for the financial agent to provide a clean interface"""
    
    def __init__(self, db: AsyncSession, user_id: int):
        self.context = FinancialAgentContext(db, user_id)
    
    async def chat(self, message: str) -> str:
        """Process a user message and return a helpful response"""
        try:
            result = await financial_agent.run(message, deps=self.context)
            print("🚀 ~ result:", result)
            return result.output
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