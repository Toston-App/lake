from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.utilities.wide_events import enrich_event, timed

router = APIRouter()


@router.get("/", response_model=list[schemas.Account])
async def read_accounts(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve all accounts for the current user.
    Returns denormalized account data including current balances and totals.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "v3_accounts"},
    )

    with timed() as t:
        accounts = await crud.account.get_multi_by_owner(
            db=db, owner_id=current_user.id
        )

    enrich_event(
        request,
        database={
            "operation": "v3_accounts",
            "duration_ms": t.ms,
            "results_count": len(accounts),
        },
    )

    return accounts
