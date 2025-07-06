from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.encoders import jsonable_encoder

from app import models, schemas
from app.api import deps
from app.crud import crud_transaction

router = APIRouter()

# TODO: this schema is returning the expense schema instead of what it correspond. Fix return types.
@router.get("/", response_model=list[schemas.Transaction])
async def read_transactions(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    page: int = 1,
    order: str = Query("desc", enum=["asc", "desc"]),
    search: Optional[str] = None,
    amount: Optional[float] = None,
    amount_operator: Optional[str] = Query(None, enum=["equal", "less", "greater"]),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    accounts: Optional[list[int]] = Query(None),
    categories: Optional[list[int]] = Query(None),
    places: Optional[list[int]] = Query(None),
) -> Any:
    """
    Retrieve transactions.
    """
    transactions = await crud_transaction.get_multi_by_owner_with_filters(
        db=db,
        owner_id=current_user.id,
        page=page,
        order=order,
        search=search,
        amount=amount,
        amount_operator=amount_operator,
        start_date=start_date,
        end_date=end_date,
        accounts=accounts,
        categories=categories,
        places=places,
    )

    return transactions
