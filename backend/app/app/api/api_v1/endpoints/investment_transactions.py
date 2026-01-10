"""
Investment transaction endpoints for the Investment Dashboard.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.investment_transaction import TransactionType
from app.schemas.investment_transaction import (
    InvestmentTransaction,
    InvestmentTransactionCreate,
    InvestmentTransactionUpdate,
    InvestmentTransactionWithAsset,
    InvestmentTransactionDeletionResponse,
)
from app.services.currency_converter import CurrencyConverter

router = APIRouter()


@router.get("", response_model=list[InvestmentTransactionWithAsset])
async def list_transactions(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = 0,
    limit: int = 100,
    holding_id: Optional[int] = Query(None, description="Filter by holding"),
    transaction_type: Optional[TransactionType] = Query(None, description="Filter by type"),
) -> Any:
    """
    List all investment transactions for the current user.
    
    Optional filters:
    - holding_id: Show transactions for a specific holding
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
            broker=tx.broker,
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
    """
    # Verify holding belongs to user
    holding = await crud.holding.get(db, id=transaction_in.holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    
    if holding.owner_id != current_user.id:
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
        broker=transaction.broker,
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

