"""
Asset management endpoints for the Investment Dashboard.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.asset import AssetClass, AssetType, Currency, Market, ASSET_TYPE_TO_CLASS
from app.schemas.asset import (
    Asset,
    AssetCreate,
    AssetUpdate,
    AssetWithPrice,
    AssetDeletionResponse,
    ExternalAssetSearchResult,
)
from app.schemas.asset_price import CurrentPrice, PriceRefreshResponse
from app.services.price_fetcher import PriceFetcher
from app.services.yahoo_finance import YahooFinanceService

router = APIRouter()


@router.get("", response_model=list[Asset])
async def list_assets(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = 0,
    limit: int = 100,
    asset_class: Optional[AssetClass] = Query(None, description="Filter by asset class"),
    asset_type: Optional[AssetType] = Query(None, description="Filter by asset type"),
    currency: Optional[Currency] = Query(None, description="Filter by currency"),
    market: Optional[Market] = Query(None, description="Filter by market"),
    is_active: Optional[bool] = Query(True, description="Filter by active status"),
) -> Any:
    """
    List all tracked assets with optional filtering.
    
    Filters:
    - asset_class: EQUITIES, FIXED_INCOME, CRYPTO, FUNDS
    - asset_type: STOCK, ETF, BOND, CETES, TREASURY, CRYPTOCURRENCY, MUTUAL_FUND, INDEX_FUND
    - currency: USD, MXN
    - market: BMV, NYSE, NASDAQ, CRYPTO, OTC
    """
    assets = await crud.asset.get_multi_filtered(
        db,
        asset_class=asset_class,
        asset_type=asset_type,
        currency=currency,
        market=market,
        is_active=is_active,
        skip=skip,
        limit=limit,
    )
    return assets


@router.post("", response_model=Asset)
async def create_asset(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_in: AssetCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create a new asset to track.
    
    The asset_class is automatically inferred from asset_type if not provided.
    """
    # Check if asset already exists
    existing = await crud.asset.get_by_symbol(db, symbol=asset_in.symbol)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Asset with symbol {asset_in.symbol.upper()} already exists",
        )
    
    # Auto-set asset_class from asset_type
    if asset_in.asset_class is None:
        asset_in.asset_class = ASSET_TYPE_TO_CLASS.get(asset_in.asset_type)
    
    asset = await crud.asset.create(db, obj_in=asset_in)
    return asset


@router.get("/search", response_model=list[Asset])
async def search_assets(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    q: str = Query(..., min_length=1, description="Search query"),
    skip: int = 0,
    limit: int = 20,
) -> Any:
    """
    Search assets by symbol or name.
    """
    assets = await crud.asset.search_assets(db, query=q, skip=skip, limit=limit)
    return assets


@router.get("/search-external", response_model=list[ExternalAssetSearchResult])
async def search_external_assets(
    current_user: models.User = Depends(deps.get_current_active_user),
    q: str = Query(..., min_length=1, description="Search query (symbol or name)"),
) -> Any:
    """
    Search for assets from external sources (Yahoo Finance).
    
    Searches both USA (NYSE, NASDAQ) and Mexican (BMV) markets.
    Results include stocks and ETFs that can be added to the portfolio.
    """
    results = await YahooFinanceService.search_symbol(q)
    
    # Filter to only include stocks and ETFs from USA and Mexico exchanges
    allowed_exchanges = {"NYQ", "NMS", "NGM", "PCX", "BTS", "MEX", "NYSE", "NASDAQ"}
    allowed_types = {"EQUITY", "ETF"}
    
    filtered_results = []
    for item in results:
        exchange = item.get("exchange", "")
        quote_type = item.get("type", "")
        
        # Include if it matches our criteria or is a Mexican stock
        is_mexican = ".MX" in (item.get("symbol") or "")
        is_allowed_exchange = exchange in allowed_exchanges or is_mexican
        is_allowed_type = quote_type in allowed_types
        
        if is_allowed_exchange and is_allowed_type:
            # Determine market and currency
            if is_mexican or exchange == "MEX":
                market = Market.BMV
                currency = Currency.MXN
                country = "MX"
            elif exchange in {"NYQ", "NYSE"}:
                market = Market.NYSE
                currency = Currency.USD
                country = "US"
            else:
                market = Market.NASDAQ
                currency = Currency.USD
                country = "US"
            
            # Determine asset type
            asset_type = AssetType.ETF if quote_type == "ETF" else AssetType.STOCK
            
            # Clean up the symbol (remove .MX suffix for internal storage)
            symbol = item.get("symbol", "").replace(".MX", "")
            
            filtered_results.append(ExternalAssetSearchResult(
                symbol=symbol,
                name=item.get("name") or symbol,
                asset_type=asset_type,
                market=market,
                currency=currency,
                country=country,
                exchange=exchange,
            ))
    
    return filtered_results


@router.get("/{asset_id}", response_model=AssetWithPrice)
async def get_asset(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get asset by ID with current price.
    """
    asset = await crud.asset.get(db, id=asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    # Get latest price
    latest_price = await crud.asset_price.get_latest_by_asset(db, asset_id=asset_id)
    
    response = AssetWithPrice(
        id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_class=asset.asset_class,
        asset_type=asset.asset_type,
        currency=asset.currency,
        market=asset.market,
        sector=asset.sector,
        country=asset.country,
        is_active=asset.is_active,
    )
    
    if latest_price:
        response.current_price = latest_price.price
        response.price_currency = latest_price.currency
        response.price_change = latest_price.change
        response.price_change_percent = latest_price.change_percent
        response.price_updated_at = latest_price.fetched_at
    
    return response


@router.put("/{asset_id}", response_model=Asset)
async def update_asset(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_id: int,
    asset_in: AssetUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update an asset.
    """
    asset = await crud.asset.get(db, id=asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    asset_in.updated_at = datetime.now(timezone.utc)
    asset = await crud.asset.update(db, db_obj=asset, obj_in=asset_in)
    return asset


@router.delete("/{asset_id}", response_model=AssetDeletionResponse)
async def delete_asset(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete an asset (soft delete by setting is_active=False).
    
    Hard delete only allowed for superusers.
    """
    asset = await crud.asset.get(db, id=asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    if crud.user.is_superuser(current_user):
        await crud.asset.remove(db, id=asset_id)
        return AssetDeletionResponse(message=f"Asset {asset.symbol} deleted")
    else:
        # Soft delete
        await crud.asset.update(db, db_obj=asset, obj_in={"is_active": False})
        return AssetDeletionResponse(message=f"Asset {asset.symbol} deactivated")


@router.get("/{asset_id}/price", response_model=CurrentPrice)
async def get_asset_price(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
    refresh: bool = Query(False, description="Force refresh price from API"),
) -> Any:
    """
    Get current price for an asset.
    
    Set refresh=true to force fetching from external API.
    """
    asset = await crud.asset.get(db, id=asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    if refresh:
        price_data = await PriceFetcher.fetch_and_store_price(db, asset)
    else:
        price_data = await PriceFetcher.get_current_price(db, asset)
    
    if not price_data:
        raise HTTPException(
            status_code=404, 
            detail=f"Could not fetch price for {asset.symbol}. This asset may require manual price entry."
        )
    
    return CurrentPrice(
        symbol=asset.symbol,
        price=price_data.price,
        currency=price_data.currency,
        price_usd=price_data.price_usd,
        price_mxn=price_data.price_mxn,
        change=price_data.change,
        change_percent=price_data.change_percent,
        fetched_at=datetime.now(timezone.utc),
    )


@router.post("/refresh-prices", response_model=PriceRefreshResponse)
async def refresh_all_prices(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    only_my_holdings: bool = Query(True, description="Only refresh assets in my portfolio"),
) -> Any:
    """
    Refresh prices for tracked assets.
    
    By default, only refreshes assets the user has holdings in.
    Set only_my_holdings=false to refresh all active assets (admin).
    """
    owner_id = current_user.id if only_my_holdings else None
    
    if not only_my_holdings and not crud.user.is_superuser(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only superusers can refresh all asset prices",
        )
    
    updated_count, failed_symbols = await PriceFetcher.refresh_all_prices(
        db, owner_id=owner_id
    )
    
    return PriceRefreshResponse(
        message=f"Refreshed {updated_count} asset prices",
        updated_count=updated_count,
        failed_symbols=failed_symbols,
    )

