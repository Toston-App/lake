from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.crud import crud_transaction
from app.schemas.transaction import AmountOperator, OrderDirection, TransactionType
from app.utilities.wide_events import enrich_event, timed
from fastapi_pagination import Page

router = APIRouter()

@router.get("/", response_model=Page[schemas.Transaction])
async def read_transactions(
    request: Request,
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
    active_filters = {
        k: v for k, v in {
            "search": search, "amount": amount, "amount_operator": amount_operator.value if amount_operator else None,
            "start_date": str(start_date) if start_date else None, "end_date": str(end_date) if end_date else None,
            "accounts": accounts, "categories": categories, "places": places,
            "transaction_type": [t.value for t in transaction_type] if transaction_type else None,
        }.items() if v is not None
    }

    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={
            "type": "paginated_transactions",
            "order": order.value,
            "filters_count": len(active_filters),
            "filters": active_filters,
        },
    )

    with timed() as t:
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

    enrich_event(
        request,
        database={
            "operation": "paginated_transactions",
            "duration_ms": t.ms,
            "results_count": len(transactions.items) if hasattr(transactions, 'items') else 0,
        },
    )

    return transactions
