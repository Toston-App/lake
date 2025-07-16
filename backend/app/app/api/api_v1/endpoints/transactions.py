from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.encoders import jsonable_encoder

from app import models, schemas
from app.api import deps
from app.crud import crud_transaction
from app.schemas.transaction import AmountOperator, OrderDirection, TransactionType
from fastapi_pagination import Page

router = APIRouter()

@router.get("/", response_model=Page[schemas.Transaction])
async def read_transactions(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    order: OrderDirection = Query(OrderDirection.desc),
    search: Optional[str] = None,
    amount: Optional[float] = None,
    amount_operator: Optional[AmountOperator] = Query(None),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    accounts: Optional[list[int]] = Query(None),
    categories: Optional[list[int]] = Query(None),
    places: Optional[list[int]] = Query(None),
    transaction_type: Optional[list[TransactionType]] = Query(None),
) -> Any:
    """
    Retrieve transactions (Expenses, Incomes, Transfers).
    """
    transactions = await crud_transaction.get_multi_by_owner_with_filters(
        db=db,
        owner_id=current_user.id,
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
    )

    return transactions
