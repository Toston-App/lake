"""
Holdings management endpoints for the Investment Dashboard.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.asset import ASSET_TYPE_TO_CLASS
from app.models.asset import AssetClass, Currency
from app.schemas.asset import AssetCreate
from app.schemas.holding import (
    Holding,
    HoldingCreate,
    HoldingUpdate,
    HoldingWithAsset,
    HoldingDeletionResponse,
)
from app.services.asset_resolver import AssetResolverService

router = APIRouter()


@router.get("", response_model=list[HoldingWithAsset])
async def list_holdings(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = 0,
    limit: int = 100,
    asset_class: Optional[AssetClass] = Query(None, description="Filter by asset class"),
    currency: Optional[Currency] = Query(None, description="Filter by asset currency"),
) -> Any:
    """
    List all holdings for the current user.
    
    Optional filters:
    - asset_class: Filter by EQUITIES, FIXED_INCOME, CRYPTO, or FUNDS
    - currency: Filter by USD or MXN exposure
    """
    holdings = await crud.holding.get_filtered(
        db,
        owner_id=current_user.id,
        asset_class=asset_class,
        currency=currency,
        skip=skip,
        limit=limit,
    )
    
    # Enrich with asset details
    result = []
    for holding in holdings:
        asset = holding.asset
        
        # Get latest price
        latest_price = await crud.asset_price.get_latest_by_asset(db, asset_id=asset.id)
        
        holding_data = HoldingWithAsset(
            id=holding.id,
            owner_id=holding.owner_id,
            asset_id=holding.asset_id,
            quantity=holding.quantity,
            avg_cost_basis=holding.avg_cost_basis,
            cost_currency=holding.cost_currency,
            total_invested=holding.total_invested,
            current_value=holding.current_value,
            current_value_mxn=holding.current_value_mxn,
            current_value_usd=holding.current_value_usd,
            unrealized_gain_loss=holding.unrealized_gain_loss,
            unrealized_gain_loss_pct=holding.unrealized_gain_loss_pct,
            # Asset details
            symbol=asset.symbol,
            asset_name=asset.name,
            asset_class=asset.asset_class,
            asset_type=asset.asset_type,
            asset_currency=asset.currency,
            market=asset.market,
            sector=asset.sector,
            country=asset.country,
        )
        
        if latest_price:
            holding_data.current_price = latest_price.price
            holding_data.price_change = latest_price.change
            holding_data.price_change_percent = latest_price.change_percent
        
        result.append(holding_data)
    
    return result


@router.post("", response_model=Holding)
async def create_holding(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    holding_in: HoldingCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create a new holding.
    
    If a holding already exists for this asset, use PUT to update it.
    """
    # Resolve asset from existing ID or external identifier
    if holding_in.asset_id is not None:
        asset = await crud.asset.get(db, id=holding_in.asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
    else:
        if not holding_in.provider or not holding_in.external_id:
            raise HTTPException(
                status_code=422,
                detail="Provide asset_id or provider+external_id",
            )

        if holding_in.provider.value == "yahoo":
            resolved_asset = await AssetResolverService.resolve_from_yahoo(holding_in.external_id)
        else:
            resolved_asset = await AssetResolverService.resolve_from_coingecko(holding_in.external_id)

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

        holding_in.asset_id = asset.id
    
    # Check if holding already exists
    existing = await crud.holding.get_by_owner_and_asset(
        db, owner_id=current_user.id, asset_id=asset.id
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"You already have a holding for {asset.symbol}.",
        )
    
    holding = await crud.holding.create_with_owner(
        db, obj_in=holding_in, owner_id=current_user.id
    )
    return holding


@router.get("/{holding_id}", response_model=HoldingWithAsset)
async def get_holding(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    holding_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a specific holding by ID.
    """
    holding = await crud.holding.get(db, id=holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    
    if holding.owner_id != current_user.id and not crud.user.is_superuser(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    asset = await crud.asset.get(db, id=holding.asset_id)
    latest_price = await crud.asset_price.get_latest_by_asset(db, asset_id=asset.id)
    
    result = HoldingWithAsset(
        id=holding.id,
        owner_id=holding.owner_id,
        asset_id=holding.asset_id,
        quantity=holding.quantity,
        avg_cost_basis=holding.avg_cost_basis,
        cost_currency=holding.cost_currency,
        total_invested=holding.total_invested,
        current_value=holding.current_value,
        current_value_mxn=holding.current_value_mxn,
        current_value_usd=holding.current_value_usd,
        unrealized_gain_loss=holding.unrealized_gain_loss,
        unrealized_gain_loss_pct=holding.unrealized_gain_loss_pct,
        symbol=asset.symbol,
        asset_name=asset.name,
        asset_class=asset.asset_class,
        asset_type=asset.asset_type,
        asset_currency=asset.currency,
        market=asset.market,
        sector=asset.sector,
        country=asset.country,
    )
    
    if latest_price:
        result.current_price = latest_price.price
        result.price_change = latest_price.change
        result.price_change_percent = latest_price.change_percent
    
    return result


@router.put("/{holding_id}", response_model=Holding)
async def update_holding(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    holding_id: int,
    holding_in: HoldingUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update a holding (quantity, cost basis, etc.)
    
    For recording buy/sell transactions, use the transactions endpoint instead.
    """
    holding = await crud.holding.get(db, id=holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    
    if holding.owner_id != current_user.id and not crud.user.is_superuser(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    holding_in.updated_at = datetime.now(timezone.utc)
    
    # Recalculate total_invested if quantity or cost basis changed
    if holding_in.quantity is not None and holding_in.avg_cost_basis is not None:
        holding_in.total_invested = holding_in.quantity * holding_in.avg_cost_basis
    elif holding_in.quantity is not None:
        holding_in.total_invested = holding_in.quantity * holding.avg_cost_basis
    elif holding_in.avg_cost_basis is not None:
        holding_in.total_invested = holding.quantity * holding_in.avg_cost_basis
    
    holding = await crud.holding.update(db, db_obj=holding, obj_in=holding_in)
    return holding


@router.delete("/{holding_id}", response_model=HoldingDeletionResponse)
async def delete_holding(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    holding_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete a holding and all associated transactions.
    """
    holding = await crud.holding.get(db, id=holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")
    
    if holding.owner_id != current_user.id and not crud.user.is_superuser(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    
    asset = await crud.asset.get(db, id=holding.asset_id)
    
    await crud.holding.remove(db, id=holding_id)
    return HoldingDeletionResponse(message=f"Holding for {asset.symbol} deleted")
