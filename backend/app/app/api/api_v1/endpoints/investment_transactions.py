"""
Investment transaction endpoints for the Investment Dashboard.
"""
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.asset import ASSET_TYPE_TO_CLASS
from app.models.investment_transaction import TransactionType
from app.schemas.investment_transaction import (
    InvestmentTransaction,
    InvestmentTransactionCreate,
    InvestmentTransactionUpdate,
    InvestmentTransactionWithAsset,
    InvestmentTransactionDeletionResponse,
    TransactionWithAssetCreate,
    TransactionWithAssetResponse,
)
from app.schemas.asset import AssetCreate
from app.schemas.holding import HoldingCreate
from app.services.asset_resolver import AssetResolverService
from app.services.currency_converter import CurrencyConverter

router = APIRouter()


@router.get("", response_model=list[InvestmentTransactionWithAsset])
async def list_transactions(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = 0,
    limit: int = 100,
    holding_id: Optional[int] = Query(None, description="Filter by holding"),
    account_id: Optional[int] = Query(None, description="Filter by account"),
    transaction_type: Optional[TransactionType] = Query(None, description="Filter by type"),
) -> Any:
    """
    List all investment transactions for the current user.

    Optional filters:
    - holding_id: Show transactions for a specific holding
    - account_id: Show transactions for a specific account
    - transaction_type: BUY, SELL, DIVIDEND, SPLIT, TRANSFER_IN, TRANSFER_OUT
    """
    if holding_id:
        transactions = await crud.investment_transaction.get_by_holding(
            db,
            holding_id=holding_id,
            owner_id=current_user.id,
            skip=skip,
            limit=limit,
        )
    elif account_id:
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
    # Verify account belongs to user
    account = await crud.account.get(db, id=transaction_in.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    # Verify holding belongs to user and matches account
    holding = await crud.holding.get(db, id=transaction_in.holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    
    if holding.owner_id != current_user.id or holding.account_id != account.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    # Get current exchange rates
    usd_mxn_rate = await CurrencyConverter.get_usd_to_mxn_rate()
    
    # Set exchange rates on transaction
    if transaction_in.exchange_rate_to_usd is None:
        if transaction_in.currency.value == "USD":
            transaction_in.exchange_rate_to_usd = 1.0
        else:
            transaction_in.exchange_rate_to_usd = 1.0 / usd_mxn_rate
    
    if transaction_in.exchange_rate_to_mxn is None:
        if transaction_in.currency.value == "MXN":
            transaction_in.exchange_rate_to_mxn = 1.0
        else:
            transaction_in.exchange_rate_to_mxn = usd_mxn_rate
    
    # Create the transaction
    transaction = await crud.investment_transaction.create_with_owner(
        db, obj_in=transaction_in, owner_id=current_user.id
    )
    
    # Update holding based on transaction type
    await _update_holding_from_transaction(db, holding, transaction)
    
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
    # Step 1: Get or create the asset
    asset_created = False
    if transaction_in.asset_id is not None:
        asset = await crud.asset.get(db, id=transaction_in.asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
    else:
        if transaction_in.provider is not None and transaction_in.external_id:
            if transaction_in.provider.value == "yahoo":
                resolved_asset = await AssetResolverService.resolve_from_yahoo(transaction_in.external_id)
            else:
                resolved_asset = await AssetResolverService.resolve_from_coingecko(transaction_in.external_id)
        else:
            raise HTTPException(
                status_code=422,
                detail="Provide asset_id or provider+external_id",
            )

        existing_asset = await crud.asset.get_by_symbol(db, symbol=resolved_asset.symbol)

        if existing_asset:
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
            asset = await crud.asset.create(db, obj_in=asset_in)
            asset_created = True
    
    # Step 2: Verify account belongs to user
    account = await crud.account.get(db, id=transaction_in.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    # Step 3: Get or create the holding for this account
    holding_created = False
    existing_holding = await crud.holding.get_by_account_and_asset(
        db, account_id=account.id, asset_id=asset.id
    )
    
    if existing_holding:
        holding = existing_holding
    else:
        # Create new holding with zero initial values
        holding_in = HoldingCreate(
            asset_id=asset.id,
            account_id=account.id,
            quantity=0.0,
            avg_cost_basis=0.0,
            cost_currency=asset.currency,
        )
        holding = await crud.holding.create_with_owner(
            db, obj_in=holding_in, owner_id=current_user.id
        )
        holding_created = True
    
    # Step 3: Get exchange rates
    usd_mxn_rate = await CurrencyConverter.get_usd_to_mxn_rate()
    transaction_currency = asset.currency
    
    exchange_rate_to_usd = transaction_in.exchange_rate_to_usd
    exchange_rate_to_mxn = transaction_in.exchange_rate_to_mxn
    
    if exchange_rate_to_usd is None:
        if transaction_currency.value == "USD":
            exchange_rate_to_usd = 1.0
        else:
            exchange_rate_to_usd = 1.0 / usd_mxn_rate
    
    if exchange_rate_to_mxn is None:
        if transaction_currency.value == "MXN":
            exchange_rate_to_mxn = 1.0
        else:
            exchange_rate_to_mxn = usd_mxn_rate
    
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
    
    transaction = await crud.investment_transaction.create_with_owner(
        db, obj_in=tx_in, owner_id=current_user.id
    )
    
    # Step 5: Update holding based on transaction type
    await _update_holding_from_transaction(db, holding, transaction)
    
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
) -> None:
    """Update holding metrics after a transaction."""
    if transaction.transaction_type == TransactionType.BUY:
        # Increase quantity and recalculate average cost
        new_total_invested = holding.total_invested + transaction.total_amount
        new_quantity = holding.quantity + transaction.quantity
        
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
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
        
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
        )
    
    elif transaction.transaction_type == TransactionType.DIVIDEND:
        # Dividends don't affect quantity or cost basis
        # They could be tracked separately for income reporting
        pass
    
    elif transaction.transaction_type == TransactionType.SPLIT:
        # Stock split: multiply quantity, divide cost basis
        # transaction.quantity represents the split ratio (e.g., 4 for 4:1 split)
        new_quantity = holding.quantity * transaction.quantity
        # Total invested stays the same, but average cost decreases
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=holding.total_invested,
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
        
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
        )


@router.get("/{transaction_id}", response_model=InvestmentTransactionWithAsset)
async def get_transaction(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a specific transaction by ID.
    """
    transaction = await crud.investment_transaction.get(db, id=transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if transaction.owner_id != current_user.id and not crud.user.is_superuser(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
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
    transaction_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete a transaction.
    
    Note: This will NOT reverse the effects on the holding.
    To properly reverse a transaction, record an opposite transaction.
    """
    transaction = await crud.investment_transaction.get(db, id=transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    if transaction.owner_id != current_user.id and not crud.user.is_superuser(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    await crud.investment_transaction.remove(db, id=transaction_id)
    return InvestmentTransactionDeletionResponse(
        message=f"Transaction {transaction_id} deleted"
    )
