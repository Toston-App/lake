from fastapi import APIRouter, Depends, HTTPException
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from typing import List, Optional
import json
from fastapi.responses import StreamingResponse

from app.api import deps
from app.core.config import settings
from app.models import User
from app.crud.crud_transaction import get_multi_by_owner_with_filters
from app.schemas.transaction import AmountOperator, OrderDirection, TransactionType
from app.schemas.ai import ClientMessage, Request, convert_to_openai_messages

from fastapi_pagination import Page

instructions = f"""
## Your identity

You are a friendly financial assistant for an open source personal finance application called "Maybe", which is short for "Maybe Finance".

## Your purpose

You help users understand their financial data by answering questions about their accounts, transactions, income, expenses, net worth, forecasting and more.

## Your rules

Follow all rules below at all times.

### General rules

- Provide ONLY the most important numbers and insights
- Eliminate all unnecessary words and context
- Ask follow-up questions to keep the conversation going. Help educate the user about their own data and entice them to ask more questions.
- Do NOT add introductions or conclusions
- Do NOT apologize or explain limitations

### Function calling rules

- Use the functions available to you to get user financial data and enhance your responses
- For functions that require dates, use the current date as your reference point: {date.today()}
- If you suspect that you do not have enough data to 100% accurately answer, be transparent about it and state exactly what the data you're presenting represents and what context it is in (i.e. date range, account, etc.)
- If you use `get_transactions` function, it will return a paginated list of transactions. You can use the `page` and `size` parameters to control the pagination. If you need more transactions, you can increase the `size` parameter or paginate through the results. `total` is the number of transactions available and `items` is the list of transactions for the current page.
    """

router = APIRouter()

# Create the agent instance
model = OpenAIModel('gpt-4o-mini', provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY))

from dataclasses import dataclass

@dataclass
class Deps:
    db: AsyncSession
    owner_id: int

agent = Agent(
    model,
    instructions=instructions,
    deps_type=Deps,
    retries=2,
)

@agent.tool
async def get_transactions(ctx: RunContext[Deps], order: OrderDirection = OrderDirection.desc,
    search: Optional[str] = None,
    amount: Optional[float] = None,
    amount_operator: Optional[AmountOperator] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    accounts: Optional[List[int]] = None,
    categories: Optional[List[int]] = None,
    places: Optional[List[int]] = None,
    transaction_type: Optional[List[TransactionType]] = None,
    page: Optional[int] = 1,
    size: Optional[int] = 50,
) -> Page[List]:
    """Get transactions (expenses, incomes and or transfers) for the user with the given filters. It will return a paginated list of transactions. You can use the `page` and `size` parameters to control the pagination. If you need more transactions, you can increase the `size` parameter or paginate through the results. `total` is the number of transactions available and `items` is the list of transactions for the current page.

    Args:
        ctx: The runtime context
        order: Order of the transactions (default: desc)
        search: Search term to filter transactions
        amount: Amount to filter by
        amount_operator: Operator for the amount filter (e.g., greater, lower, equal)
        start_date: Start date of the transactions (YYYY-MM-DD)
        end_date: End date of the transactions (YYYY-MM-DD)
        accounts: List of account IDs to filter by
        categories: List of category IDs to filter by
        places: List of place IDs to filter by
        transaction_type: List of transaction types to filter by (e.g., expense, income, transfer). If you want to get all types, pass an empty list.
        page: Page number for pagination (default: 1)
        size: Number of items per page for pagination (default: 50)
    """
    try:
        res = await get_multi_by_owner_with_filters(
            db=ctx.deps.db,
            owner_id=ctx.deps.owner_id,
            order=order,
            search=search,
            amount=amount,
            amount_operator=amount_operator,
            start_date=start_date,
            end_date=end_date,
            accounts=accounts,
            categories=categories,
            places=places,
            transaction_type=transaction_type,
            page=page,
            size=size,
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def stream_generator(agent, openai_messages: List[ClientMessage], deps: Deps):
    """Generator function to stream the agent's response."""
    async with agent.run_stream(openai_messages, deps=deps) as result:
        async for chunk in result.stream_text(delta=True):
            print("🚀 ~ chunk:", f"0:{json.dumps(chunk)}")
            yield f"0:{json.dumps(chunk)}\n"

@router.post("/chat")
async def chat(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.get_current_active_user),
    request: Request,
) -> dict:
    """Chat with the financial agent."""
    try:
        prompt = request.messages[-1].content
        depsx = Deps(db=db, owner_id=current_user.id)
        result = await agent.run(
            prompt, deps=depsx
        )
        # Run the agent with the dependencies
        return {"response": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.get_current_active_user),
    request: Request,
):
    """Chat with the financial agent and stream the response."""
    try:
        messages = request.messages

        depsx = Deps(db=db, owner_id=current_user.id)
        response =  StreamingResponse(
            stream_generator(agent, messages[-1].content, depsx),
            media_type="text/event-stream",
        )
        response.headers['x-vercel-ai-data-stream'] = 'v1'

        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
