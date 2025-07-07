from fastapi import APIRouter, Depends, HTTPException
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from typing import List, Optional, Dict, Any, Union

from app.api import deps
from app.core.config import settings
from app.models import User
from app.crud.crud_transaction import get_multi_by_owner_with_filters
from app.api.api_v1.endpoints.transactions import read_transactions
from app.schemas.transaction import AmountOperator, OrderDirection, TransactionType

from app.models.expense import Expense
from app.models.income import Income
from app.models.transfer import Transfer
from fastapi_pagination import Page

instructions = """## Your identity

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
      - Do NOT apologize or explain limitations"""

router = APIRouter()

# Create the agent instance
model = OpenAIModel('gpt-4o-mini', provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY))
# agent = Agent(
#     model=model,
#     deps_type=Dict[str, Any],
#     system_prompt=(
    #     """## Your identity

    #   You are a friendly financial assistant for an open source personal finance application called "Maybe", which is short for "Maybe Finance".

    #   ## Your purpose

    #   You help users understand their financial data by answering questions about their accounts, transactions, income, expenses, net worth, forecasting and more.

    #   ## Your rules

    #   Follow all rules below at all times.

    #   ### General rules

    #   - Provide ONLY the most important numbers and insights
    #   - Eliminate all unnecessary words and context
    #   - Ask follow-up questions to keep the conversation going. Help educate the user about their own data and entice them to ask more questions.
    #   - Do NOT add introductions or conclusions
    #   - Do NOT apologize or explain limitations"""
#     ),
# )


# @agent.tool
# async def get_transactions(
#     ctx: RunContext[Dict[str, Any]],
#     order: OrderDirection = OrderDirection.desc,
#     search: Optional[str] = None,
#     amount: Optional[float] = None,
#     amount_operator: Optional[AmountOperator] = None,
#     start_date: Optional[date] = None,
#     end_date: Optional[date] = None,
#     accounts: Optional[List[int]] = None,
#     categories: Optional[List[int]] = None,
#     places: Optional[List[int]] = None,
#     transaction_type: Optional[List[TransactionType]] = None,
# ) -> List:
#     """Get transactions for the user with the given filters.
    
#     Args:
#         ctx: The runtime context
#         order: Order of the transactions (default: desc)
#         search: Search term to filter transactions
#         amount: Amount to filter by
#         amount_operator: Operator for the amount filter (e.g., gt, lt, eq)
#         start_date: Start date of the transactions
#         end_date: End date of the transactions
#         accounts: List of account IDs to filter by
#         categories: List of category IDs to filter by
#         places: List of place IDs to filter by
#         transaction_type: List of transaction types to filter by
#     """
#     # Get db and owner_id from the context dependencies
#     db = ctx.deps['db']
#     owner_id = ctx.deps['owner_id']

#     return await get_multi_by_owner_with_filters(
#         db=db,
#         owner_id=owner_id,
#         order=order,
#         search=search,
#         amount=amount,
#         amount_operator=amount_operator,
#         start_date=start_date,
#         end_date=end_date,
#         accounts=accounts,
#         categories=categories,
#         places=places,
#         transaction_type=transaction_type,
#     )


# @router.post("/chat")
# async def chat(
#     *,
#     db: AsyncSession = Depends(deps.get_db),
#     current_user: User = Depends(deps.get_current_active_user),
#     prompt: str,
# ):
#     """Chat with the financial agent."""
#     try:
#         # Run the agent with the dependencies
#         response = await agent.run(prompt, deps={"db": db, "owner_id": current_user.id})
#         return {"response": response}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

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
) -> Page[Union[Expense, Income, Transfer]]:
    """Get transactions (expenses, incomes and or transfers) for the user with the given filters.

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
        transaction_type: List of transaction types to filter by (e.g., expense, income, transfer)
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

@router.post("/chat")
async def chat(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.get_current_active_user),
    prompt: str,
) -> dict:
    """Chat with the financial agent."""
    try:
        deps = Deps(db=db, owner_id=current_user.id)
        result = await agent.run(
            prompt, deps=deps
        )
        # Run the agent with the dependencies
        return {"response": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# import asyncio
# from dataclasses import dataclass
# from httpx import AsyncClient
# from pydantic import BaseModel

# client = AsyncClient()

# @dataclass
# class Deps:
#     client: AsyncClient

# weather_agent = Agent(
#     model,
#     instructions='Be concise, reply with one sentence.',
#     deps_type=Deps,
#     retries=2,
# )


# class LatLng(BaseModel):
#     lat: float
#     lng: float


# @weather_agent.tool
# async def get_lat_lng(ctx: RunContext[Deps], location_description: str) -> LatLng:
#     """Get the latitude and longitude of a location.

#     Args:
#         ctx: The context.
#         location_description: A description of a location.
#     """
#     # NOTE: the response here will be random, and is not related to the location description.
#     r = await ctx.deps.client.get(
#         'https://demo-endpoints.pydantic.workers.dev/latlng',
#         params={'location': location_description},
#     )
#     r.raise_for_status()
#     return LatLng.model_validate_json(r.content)


# @weather_agent.tool
# async def get_weather(ctx: RunContext[Deps], lat: float, lng: float) -> dict[str, Any]:
#     """Get the weather at a location.

#     Args:
#         ctx: The context.
#         lat: Latitude of the location.
#         lng: Longitude of the location.
#     """
#     # NOTE: the responses here will be random, and are not related to the lat and lng.
#     temp_response, descr_response = await asyncio.gather(
#         ctx.deps.client.get(
#             'https://demo-endpoints.pydantic.workers.dev/number',
#             params={'min': 10, 'max': 30},
#         ),
#         ctx.deps.client.get(
#             'https://demo-endpoints.pydantic.workers.dev/weather',
#             params={'lat': lat, 'lng': lng},
#         ),
#     )
#     temp_response.raise_for_status()
#     descr_response.raise_for_status()
#     return {
#         'temperature': f'{temp_response.text} °C',
#         'description': descr_response.text,
#     }

# @router.post("/chat2")
# async def chat(
#     *,
#     db: AsyncSession = Depends(deps.get_db),
#     current_user: User = Depends(deps.get_current_active_user),
#     prompt: str,
# ):
#     """Chat with the financial agent."""
#     try:
#         deps = Deps(client=client)
#         result = await weather_agent.run(
#             prompt, deps=deps
#         )
#         return {"response": result}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
