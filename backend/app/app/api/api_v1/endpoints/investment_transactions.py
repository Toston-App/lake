"""
Investment transaction endpoints for the Investment Dashboard.
"""
import math
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app import crud, models
from app.api import deps
from app.models.asset import ASSET_TYPE_TO_CLASS
from app.models.investment_transaction import TransactionType
from app.schemas.investment_transaction import (
    InvestmentTransaction,
    InvestmentTransactionCreate,
    InvestmentTransactionWithAsset,
    InvestmentTransactionDeletionResponse,
    TransactionWithAssetCreate,
    TransactionWithAssetResponse,
)
from app.schemas.asset import AssetCreate
from app.schemas.holding import HoldingCreate
from app.services.asset_resolver import AssetResolverService
from app.services.currency_converter import CurrencyConverter
from app.services.investment_rate_limiter import enforce_investment_rate_limit

router = APIRouter()


@router.get("", response_model=list[InvestmentTransactionWithAsset])
async def list_transactions(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    holding_id: Optional[int] = Query(None, ge=1, description="Filter by holding"),
    account_id: Optional[int] = Query(None, ge=1, description="Filter by account"),
    transaction_type: Optional[TransactionType] = Query(None, description="Filter by type"),
) -> Any:
    """
    List all investment transactions for the current user.

    Optional filters:
    - holding_id: Show transactions for a specific holding
    - account_id: Show transactions for a specific account
    - transaction_type: BUY, SELL, DIVIDEND, SPLIT, TRANSFER_IN, TRANSFER_OUT
    """
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

    # Enrich with asset details
    result = []
    for tx in transactions:
        holding = await crud.holding.get(db, id=tx.holding_id)
        asset = await crud.asset.get(db, id=holding.asset_id) if holding else None

        tx_data = InvestmentTransactionWithAsset(
            id=tx.id,
            owner_id=tx.owner_id,
            account_id=tx.account_id,
            holding_id=tx.holding_id,
            transaction_type=tx.transaction_type,
            quantity=tx.quantity,
            price_per_unit=tx.price_per_unit,
            currency=tx.currency,
            total_amount=tx.total_amount,
            fees=tx.fees,
            exchange_rate_to_usd=tx.exchange_rate_to_usd,
            exchange_rate_to_mxn=tx.exchange_rate_to_mxn,
            notes=tx.notes,
            executed_at=tx.executed_at,
            symbol=asset.symbol if asset else None,
            asset_name=asset.name if asset else None,
        )
        result.append(tx_data)

    return result


@router.post("", response_model=InvestmentTransaction)
async def create_transaction(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_in: InvestmentTransactionCreate,
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
    # Verify account belongs to user.
    account = await crud.account.get_by_id(
        db, id=transaction_in.account_id, owner_id=current_user.id
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    candidate_holding = await crud.holding.get_by_id_and_owner(
        db,
        holding_id=transaction_in.holding_id,
        owner_id=current_user.id,
    )
    if not candidate_holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    if candidate_holding.account_id != account.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    # Do not hold a database row lock while waiting for an upstream FX service.
    usd_mxn_rate = await CurrencyConverter.get_usd_to_mxn_rate()
    
    # Lock the owner-scoped holding so concurrent sells cannot overspend it.
    holding = await crud.holding.get_for_update_by_owner(
        db, holding_id=transaction_in.holding_id, owner_id=current_user.id
    )
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    
    if holding.owner_id != current_user.id or holding.account_id != account.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    await _validate_transaction_against_holding(holding, transaction_in)

    # Currency and exchange rates are server-owned financial fields.
    transaction_currency = holding.asset.currency
    transaction_in = transaction_in.copy(update={
        "currency": transaction_currency,
        "exchange_rate_to_usd": 1.0 if transaction_currency.value == "USD" else 1.0 / usd_mxn_rate,
        "exchange_rate_to_mxn": 1.0 if transaction_currency.value == "MXN" else usd_mxn_rate,
    })
    
    # Create the transaction
    transaction = await crud.investment_transaction.create_with_owner(
        db, obj_in=transaction_in, owner_id=current_user.id, commit=False
    )
    
    # Update holding based on transaction type
    await _update_holding_from_transaction(db, holding, transaction, commit=False)
    await db.commit()
    await db.refresh(transaction)
    return transaction


@router.post("/with-asset", response_model=TransactionWithAssetResponse)
async def create_transaction_with_asset(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_in: TransactionWithAssetCreate,
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
    # Validate ownership before resolving or creating any shared asset state.
    account = await crud.account.get_by_id(
        db, id=transaction_in.account_id, owner_id=current_user.id
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Step 1: Get or create the asset
    asset_created = False
    if transaction_in.asset_id is not None:
        asset = await crud.asset.get(db, id=transaction_in.asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
    else:
        if transaction_in.provider is not None and transaction_in.external_id:
            enforce_investment_rate_limit(
                f"user:{current_user.id}:resolve-asset", 1.0
            )
            if transaction_in.provider.value == "yahoo":
                resolved_asset = await AssetResolverService.resolve_from_yahoo(transaction_in.external_id)
            else:
                resolved_asset = await AssetResolverService.resolve_from_coingecko(transaction_in.external_id)
        else:
            raise HTTPException(
                status_code=422,
                detail="Provide asset_id or provider+external_id",
            )

        if resolved_asset.coingecko_id:
            existing_asset = await crud.asset.get_by_coingecko_id(
                db, coingecko_id=resolved_asset.coingecko_id
            )
            if existing_asset is None and await crud.asset.get_by_symbol(
                db, symbol=resolved_asset.symbol
            ):
                raise HTTPException(
                    status_code=409,
                    detail="External asset symbol conflicts with an existing global asset",
                )
        else:
            existing_asset = await crud.asset.get_by_symbol(db, symbol=resolved_asset.symbol)

        if existing_asset:
            if (
                existing_asset.asset_type != resolved_asset.asset_type
                or existing_asset.market != resolved_asset.market
                or existing_asset.currency != resolved_asset.currency
            ):
                raise HTTPException(
                    status_code=409,
                    detail="External asset conflicts with an existing global asset",
                )
            asset = existing_asset
        else:
            asset_in = AssetCreate(
                symbol=resolved_asset.symbol,
                name=resolved_asset.name,
                asset_type=resolved_asset.asset_type,
                asset_class=ASSET_TYPE_TO_CLASS.get(resolved_asset.asset_type),
                currency=resolved_asset.currency,
                market=resolved_asset.market,
                country=resolved_asset.country,
                coingecko_id=resolved_asset.coingecko_id,
            )
            try:
                asset = await crud.asset.create(db, obj_in=asset_in, commit=False)
            except IntegrityError:
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Asset was concurrently created; retry the request",
                )
            asset_created = True

    # Resolve FX before locking an existing holding.
    usd_mxn_rate = await CurrencyConverter.get_usd_to_mxn_rate()
    
    # Step 3: Get or create the holding for this account
    holding_created = False
    existing_holding = await crud.holding.get_by_account_and_asset(
        db,
        account_id=account.id,
        asset_id=asset.id,
        owner_id=current_user.id,
    )
    
    if existing_holding:
        holding = await crud.holding.get_for_update_by_owner(
            db, holding_id=existing_holding.id, owner_id=current_user.id
        )
        if not holding:
            raise HTTPException(status_code=404, detail="Holding not found")
    else:
        if not asset.is_active:
            raise HTTPException(status_code=409, detail="Asset is inactive")
        if transaction_in.transaction_type not in (
            TransactionType.BUY,
            TransactionType.TRANSFER_IN,
        ):
            raise HTTPException(
                status_code=400,
                detail="A new holding must start with a buy or transfer_in transaction",
            )
        # Create new holding with zero initial values
        holding_in = HoldingCreate(
            asset_id=asset.id,
            account_id=account.id,
            quantity=0.0,
            avg_cost_basis=0.0,
            cost_currency=asset.currency,
        )
        try:
            holding = await crud.holding.create_with_owner(
                db, obj_in=holding_in, owner_id=current_user.id, commit=False
            )
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="A holding for this asset already exists in the account; retry the request",
            )
        holding_created = True
    
    # Step 3: Get exchange rates
    transaction_currency = asset.currency
    
    exchange_rate_to_usd = 1.0 if transaction_currency.value == "USD" else 1.0 / usd_mxn_rate
    exchange_rate_to_mxn = 1.0 if transaction_currency.value == "MXN" else usd_mxn_rate
    
    # Step 4: Create the transaction
    tx_in = InvestmentTransactionCreate(
        holding_id=holding.id,
        account_id=account.id,
        transaction_type=transaction_in.transaction_type,
        quantity=transaction_in.quantity,
        price_per_unit=transaction_in.price_per_unit,
        currency=transaction_currency,
        fees=transaction_in.fees,
        exchange_rate_to_usd=exchange_rate_to_usd,
        exchange_rate_to_mxn=exchange_rate_to_mxn,
        executed_at=transaction_in.executed_at,
        notes=transaction_in.notes,
    )

    await _validate_transaction_against_holding(holding, tx_in)
    
    transaction = await crud.investment_transaction.create_with_owner(
        db, obj_in=tx_in, owner_id=current_user.id, commit=False
    )
    
    # Step 5: Update holding based on transaction type
    await _update_holding_from_transaction(db, holding, transaction, commit=False)
    await db.commit()
    await db.refresh(transaction)
    
    return TransactionWithAssetResponse(
        transaction=InvestmentTransaction(
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
            exchange_rate_to_usd=transaction.exchange_rate_to_usd,
            exchange_rate_to_mxn=transaction.exchange_rate_to_mxn,
            notes=transaction.notes,
            executed_at=transaction.executed_at,
        ),
        asset_created=asset_created,
        holding_created=holding_created,
        asset_id=asset.id,
        holding_id=holding.id,
    )


async def _update_holding_from_transaction(
    db: AsyncSession,
    holding: models.Holding,
    transaction: models.InvestmentTransaction,
    commit: bool = True,
) -> None:
    """Update holding metrics after a transaction."""
    if transaction.transaction_type == TransactionType.BUY:
        # Increase quantity and recalculate average cost
        new_total_invested = holding.total_invested + transaction.total_amount
        new_quantity = holding.quantity + transaction.quantity
        _validate_holding_state(new_quantity, new_total_invested)
        
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
            commit=commit,
        )
    
    elif transaction.transaction_type == TransactionType.SELL:
        # Decrease quantity, proportionally reduce invested amount
        if transaction.quantity > holding.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot sell {transaction.quantity} shares. Only {holding.quantity} available.",
            )
        
        # Calculate proportional reduction in invested amount
        proportion_sold = transaction.quantity / holding.quantity
        amount_to_remove = holding.total_invested * proportion_sold
        
        new_quantity = holding.quantity - transaction.quantity
        new_total_invested = holding.total_invested - amount_to_remove
        _validate_holding_state(new_quantity, new_total_invested)
        
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
            commit=commit,
        )
    
    elif transaction.transaction_type == TransactionType.DIVIDEND:
        # Dividends don't affect quantity or cost basis
        # They could be tracked separately for income reporting
        pass
    
    elif transaction.transaction_type == TransactionType.SPLIT:
        # Stock split: multiply quantity, divide cost basis
        # transaction.quantity represents the split ratio (e.g., 4 for 4:1 split)
        new_quantity = holding.quantity * transaction.quantity
        _validate_holding_state(new_quantity, holding.total_invested)
        # Total invested stays the same, but average cost decreases
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=holding.total_invested,
            commit=commit,
        )
    
    elif transaction.transaction_type in (TransactionType.TRANSFER_IN, TransactionType.TRANSFER_OUT):
        # Transfers: adjust quantity without changing cost basis per share
        multiplier = 1 if transaction.transaction_type == TransactionType.TRANSFER_IN else -1
        new_quantity = holding.quantity + (transaction.quantity * multiplier)
        
        if new_quantity < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Transfer would result in negative quantity.",
            )
        
        # Adjust total invested proportionally
        if transaction.transaction_type == TransactionType.TRANSFER_IN:
            new_total_invested = holding.total_invested + transaction.total_amount
        else:
            proportion = transaction.quantity / holding.quantity
            new_total_invested = holding.total_invested * (1 - proportion)

        _validate_holding_state(new_quantity, new_total_invested)
        
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
            commit=commit,
        )


def _validate_holding_state(quantity: float, total_invested: float) -> None:
    if (
        not math.isfinite(quantity)
        or not math.isfinite(total_invested)
        or quantity < 0
        or quantity > 1e15
        or total_invested < 0
        or total_invested > 1e30
    ):
        raise HTTPException(
            status_code=422,
            detail="Transaction would create an unsafe holding value",
        )


async def _validate_transaction_against_holding(
    holding: models.Holding,
    transaction: InvestmentTransactionCreate,
) -> None:
    """Validate position-changing rules before any ledger row is written."""
    if transaction.transaction_type in (TransactionType.SELL, TransactionType.TRANSFER_OUT):
        if transaction.quantity > holding.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot remove {transaction.quantity} units. Only {holding.quantity} available.",
            )

    if transaction.transaction_type == TransactionType.SPLIT and transaction.quantity <= 0:
        raise HTTPException(status_code=400, detail="Split ratio must be positive")


@router.get("/{transaction_id}", response_model=InvestmentTransactionWithAsset)
async def get_transaction(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a specific transaction by ID.
    """
    if crud.user.is_superuser(current_user):
        transaction = await crud.investment_transaction.get(db, id=transaction_id)
    else:
        transaction = await crud.investment_transaction.get_by_id_and_owner(
            db, transaction_id=transaction_id, owner_id=current_user.id
        )
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    holding = await crud.holding.get(db, id=transaction.holding_id)
    asset = await crud.asset.get(db, id=holding.asset_id) if holding else None
    
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
        exchange_rate_to_usd=transaction.exchange_rate_to_usd,
        exchange_rate_to_mxn=transaction.exchange_rate_to_mxn,
        notes=transaction.notes,
        executed_at=transaction.executed_at,
        symbol=asset.symbol if asset else None,
        asset_name=asset.name if asset else None,
    )


@router.delete("/{transaction_id}", response_model=InvestmentTransactionDeletionResponse)
async def delete_transaction(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Investment transactions are immutable. Record a correcting transaction instead.
    """
    if crud.user.is_superuser(current_user):
        transaction = await crud.investment_transaction.get(db, id=transaction_id)
    else:
        transaction = await crud.investment_transaction.get_by_id_and_owner(
            db, transaction_id=transaction_id, owner_id=current_user.id
        )
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    raise HTTPException(
        status_code=409,
        detail=(
            "Investment transactions are immutable because deleting one would corrupt "
            "the holding cost basis. Record a correcting transaction instead."
        ),
    )
