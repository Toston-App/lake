"""
Investment transaction endpoints for the Investment Dashboard.
"""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.models.investment_transaction import TransactionType
from app.schemas.investment_transaction import (
    InvestmentTransaction,
    InvestmentTransactionCreate,
    InvestmentTransactionDeletionResponse,
    InvestmentTransactionWithAsset,
    TransactionWithAssetCreate,
    TransactionWithAssetResponse,
)
from app.services import investment_ledger as ledger
from app.services import investments as investment_service
from app.services.errors import ServiceError
from app.utilities.investment_telemetry import (
    add_investment_context,
    complete_investment_event,
    fail_investment_event,
    investment_stage,
)

router = APIRouter()


async def _trusted_usd_mxn_rate() -> Decimal:
    # FxRateUnavailable is a ServiceError: the API reports it as a 503.
    return await investment_service.usd_mxn_rate()


@router.get("", response_model=list[InvestmentTransactionWithAsset])
async def list_transactions(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    holding_id: int | None = Query(None, ge=1, description="Filter by holding"),
    account_id: int | None = Query(None, ge=1, description="Filter by account"),
    transaction_type: TransactionType | None = Query(
        None, description="Filter by type"
    ),
) -> Any:
    """
    List all investment transactions for the current user.

    Optional filters:
    - holding_id: Show transactions for a specific holding
    - account_id: Show transactions for a specific account
    - transaction_type: BUY, SELL, DIVIDEND, SPLIT, TRANSFER_IN, TRANSFER_OUT
    """
    add_investment_context(
        request,
        holding_id=holding_id,
        account_id=account_id,
        transaction_type=transaction_type,
    )
    with investment_stage(request, "database_query"):
        if holding_id is not None:
            transactions = await crud.investment_transaction.get_by_holding(
                db,
                holding_id=holding_id,
                owner_id=current_user.id,
                skip=skip,
                limit=limit,
            )
        elif account_id is not None:
            transactions = await crud.investment_transaction.get_by_account(
                db,
                account_id=account_id,
                owner_id=current_user.id,
                skip=skip,
                limit=limit,
            )
        elif transaction_type:
            transactions = await crud.investment_transaction.get_by_type(
                db,
                owner_id=current_user.id,
                transaction_type=transaction_type,
                skip=skip,
                limit=limit,
            )
        else:
            transactions = await crud.investment_transaction.get_by_owner(
                db,
                owner_id=current_user.id,
                skip=skip,
                limit=limit,
            )

    # Relationships are eagerly loaded in the CRUD query to avoid per-row queries.
    result = [investment_service.transaction_with_asset(tx) for tx in transactions]
    complete_investment_event(request, result_count=len(result))
    return result


@router.post("", response_model=InvestmentTransaction)
async def create_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_in: InvestmentTransactionCreate,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Record a new investment transaction (buy, sell, dividend, etc.)

    This will automatically update the holding's:
    - quantity (for BUY/SELL)
    - average cost basis (for BUY)
    - total invested amount

    And update the account's total_investments.
    """
    add_investment_context(
        request,
        account_id=transaction_in.account_id,
        holding_id=transaction_in.holding_id,
        transaction_type=transaction_in.transaction_type,
    )
    try:
        transaction = await ledger.record_transaction(
            db,
            current_user,
            transaction_in,
            idempotency_key=idempotency_key,
            stage=lambda name: investment_stage(request, name),
            on_asset=lambda asset: add_investment_context(
                request, asset_id=asset.id, symbol=asset.symbol
            ),
        )
    except ServiceError as exc:
        fail_investment_event(
            request, reason=exc.reason, stage=getattr(exc, "stage", None)
        )
        raise
    add_investment_context(request, transaction_id=transaction.id)
    complete_investment_event(request)
    return transaction


@router.post("/with-asset", response_model=TransactionWithAssetResponse)
async def create_transaction_with_asset(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_in: TransactionWithAssetCreate,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Record a transaction with asset creation.

    This endpoint will:
    1. Create the asset if it doesn't exist (or use existing)
    2. Create the holding for the user if it doesn't exist (or use existing)
    3. Record the transaction
    4. Update the holding's cost basis and quantity

    This is ideal for quickly adding new investments without first
    manually creating assets and holdings.
    """
    add_investment_context(
        request,
        account_id=transaction_in.account_id,
        asset_id=transaction_in.asset_id,
        provider=transaction_in.provider,
        transaction_type=transaction_in.transaction_type,
    )
    try:
        result = await ledger.record_transaction_with_asset(
            db,
            current_user,
            transaction_in,
            idempotency_key=idempotency_key,
            stage=lambda name: investment_stage(request, name),
            on_asset=lambda asset: add_investment_context(
                request, asset_id=asset.id, symbol=asset.symbol
            ),
        )
    except ServiceError as exc:
        fail_investment_event(
            request, reason=exc.reason, stage=getattr(exc, "stage", None)
        )
        raise
    add_investment_context(
        request,
        asset_id=result.asset_id,
        holding_id=result.holding_id,
        transaction_id=result.transaction.id,
    )
    complete_investment_event(
        request,
        asset_created=result.asset_created,
        holding_created=result.holding_created,
    )
    return TransactionWithAssetResponse(
        transaction=InvestmentTransaction.model_validate(result.transaction),
        asset_created=result.asset_created,
        holding_created=result.holding_created,
        asset_id=result.asset_id,
        holding_id=result.holding_id,
    )


@router.get("/{transaction_id}", response_model=InvestmentTransactionWithAsset)
async def get_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a specific transaction by ID.
    """
    add_investment_context(request, transaction_id=transaction_id)
    with investment_stage(request, "database_query"):
        if crud.user.is_superuser(current_user):
            transaction = await crud.investment_transaction.get(db, id=transaction_id)
        else:
            transaction = await crud.investment_transaction.get_by_id_and_owner(
                db, transaction_id=transaction_id, owner_id=current_user.id
            )
    if not transaction:
        fail_investment_event(request, reason="transaction_not_found")
        raise HTTPException(status_code=404, detail="Transaction not found")

    holding = await crud.holding.get(db, id=transaction.holding_id)
    asset = await crud.asset.get(db, id=holding.asset_id) if holding else None

    add_investment_context(
        request,
        account_id=transaction.account_id,
        holding_id=transaction.holding_id,
        asset_id=holding.asset_id if holding else None,
        symbol=asset.symbol if asset else None,
        transaction_type=transaction.transaction_type,
    )
    complete_investment_event(request)
    return InvestmentTransactionWithAsset(
        id=transaction.id,
        owner_id=transaction.owner_id,
        account_id=transaction.account_id,
        holding_id=transaction.holding_id,
        transaction_type=transaction.transaction_type,
        quantity=transaction.quantity,
        price_per_unit=transaction.price_per_unit,
        currency=transaction.currency,
        total_amount=transaction.total_amount,
        fees=transaction.fees,
        affects_cash_balance=transaction.affects_cash_balance,
        exchange_rate_to_usd=transaction.exchange_rate_to_usd,
        exchange_rate_to_mxn=transaction.exchange_rate_to_mxn,
        notes=transaction.notes,
        executed_at=transaction.executed_at,
        symbol=asset.symbol if asset else None,
        asset_name=asset.name if asset else None,
    )


@router.delete(
    "/{transaction_id}", response_model=InvestmentTransactionDeletionResponse
)
async def delete_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Investment transactions are immutable. Record a correcting transaction instead.
    """
    add_investment_context(request, transaction_id=transaction_id)
    with investment_stage(request, "database_query"):
        if crud.user.is_superuser(current_user):
            transaction = await crud.investment_transaction.get(db, id=transaction_id)
        else:
            transaction = await crud.investment_transaction.get_by_id_and_owner(
                db, transaction_id=transaction_id, owner_id=current_user.id
            )
    if not transaction:
        fail_investment_event(request, reason="transaction_not_found")
        raise HTTPException(status_code=404, detail="Transaction not found")

    fail_investment_event(
        request, reason="transaction_immutable", stage="immutability_check"
    )
    raise HTTPException(
        status_code=409,
        detail=(
            "Investment transactions are immutable because deleting one would corrupt "
            "the holding cost basis. Record a correcting transaction instead."
        ),
    )
