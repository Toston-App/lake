from app.ai.prompt import Request
from fastapi import APIRouter, Depends, HTTPException
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse
from datetime import date
from typing import List, Optional, Dict, Any, Union
from dataclasses import dataclass
import json
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart

from app.api import deps
from app.core.config import settings
from app.models import User
from app.crud.crud_transaction import get_multi_by_owner_with_filters
from app.crud.crud_category import category as crud_category
from app.crud.crud_subcategory import subcategory as crud_subcategory
from app.api.api_v1.endpoints.transactions import read_transactions
from app.schemas.transaction import AmountOperator, OrderDirection, TransactionType
from app.schemas.transaction import Transaction
from pydantic import BaseModel


class AgentSubcategory(BaseModel):
    id: int
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    is_default: bool = False

    class Config:
        from_attributes = True


class AgentCategory(BaseModel):
    id: int
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    is_default: bool = False
    is_income: bool = False
    subcategories: List["AgentSubcategory"] = []

    class Config:
        from_attributes = True


from app.models.expense import Expense
from app.models.income import Income
from app.models.transfer import Transfer
from fastapi_pagination import Page
from app.utilities.redis_message_history import get_message_history, store_message_history

instructions = f"""
## Your identity

You are a friendly financial assistant for an open source personal finance application called "Clever", which is short for "Cleverbilling".

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

model = OpenAIModel('gpt-4o-mini', provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY))

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
    # Get db and owner_id from the context dependencies
    print("🚀 ~ ctx:", ctx)
    print("🚀 ~ ctx.deps.owner_id:", ctx.deps.owner_id)

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
        print("🚀 ~ res:", res)

        return res
    except Exception as e:
        print("🚀 ~ Exception in get_transactions:", e)
        raise HTTPException(status_code=500, detail=str(e))


@agent.tool
async def get_categories(ctx: RunContext[Deps]) -> List[AgentCategory]:
    """Get all the categories with all subcategories. Use this only if you need to get a lot of categories.

    Args:
        ctx: The runtime context
    """
    categories = await crud_category.get_multi_by_owner(db=ctx.deps.db, owner_id=ctx.deps.owner_id)
    return [AgentCategory.from_orm(c) for c in categories]


@agent.tool
async def get_category(ctx: RunContext[Deps], id: int) -> AgentCategory:
    """Get a category with all subcategories by its ID. Use this only if you need to get few categories.

    Args:
        ctx: The runtime context
        id: The ID of the category to get
    """
    try:
        category_model = await crud_category.get(db=ctx.deps.db, id=id)
        if not category_model:
            raise HTTPException(status_code=404, detail="Category not found")
        return AgentCategory.from_orm(category_model)
    except Exception as e:
        print("🚀 ~ Exception in get_category:", e)
        raise HTTPException(status_code=500, detail=str(e))

@agent.tool
async def get_subcategory(ctx: RunContext[Deps], id: int) -> AgentSubcategory:
    """Get a subcategory by its ID.

    Args:
        ctx: The runtime context
        id: The ID of the subcategory to get
    """
    try:
        subcategory_model = await crud_subcategory.get(db=ctx.deps.db, id=id)
        if not subcategory_model:
            raise HTTPException(status_code=404, detail="Subcategory not found")
        return AgentSubcategory.from_orm(subcategory_model)
    except Exception as e:
        print("🚀 ~ Exception in get_subcategory:", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat")
async def chat(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.get_current_active_user),
    prompt: str,
    session_id: str
) -> Dict[str, Any]:
    """Chat with the financial agent."""
    try:
        deps = Deps(db=db, owner_id=current_user.id)
        # Retrieve message history as ModelMessage objects
        history = await get_message_history(current_user.id, session_id)
        # Append new user message as ModelRequest
        history.append(ModelRequest(parts=[UserPromptPart(content=prompt)]))
        # Run the agent with the full history
        result = await agent.run(
            prompt, deps=deps, message_history=history
        )
        # Append agent response as ModelResponse
        history.append(ModelResponse(parts=[TextPart(content=result)]))
        # Store updated history
        await store_message_history(current_user.id, session_id, history)
        print("🚀 ~ result:", result)
        return {"response": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat/stream")
async def stream_chat(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.get_current_active_user),
    request: Request,
) -> StreamingResponse:
    """
    This endpoint streams the agent's response.
    """
    try:
        deps = Deps(db=db, owner_id=current_user.id)

        session_id = request.id
        # Extract text from the parts array of the last message
        user_prompt = request.messages[-1].content

        history = await get_message_history(current_user.id, session_id)
        history.append(ModelRequest(parts=[UserPromptPart(content=user_prompt)]))

        async def generate_stream() -> Any:
            async with agent.run_stream(user_prompt, deps=deps, message_history=history) as response:
                async for chunk in response.stream_text(delta=True):
                    print("🚀 ~ chunk:", chunk)
                    yield f'0:{json.dumps(chunk)}\n'

                history.append(ModelResponse(parts=[TextPart(content=response.all_messages()[-1].parts[0].content)]))
                await store_message_history(current_user.id, session_id, history)

        response = StreamingResponse(generate_stream(), media_type="text/event-stream")
        response.headers['x-vercel-ai-data-stream'] = 'v1'
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
