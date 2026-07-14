"""
Asset management endpoints for the Investment Dashboard.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.models.asset import (
    ASSET_TYPE_TO_CLASS,
    AssetClass,
    AssetType,
    Currency,
    Market,
)
from app.schemas.asset import (
    Asset,
    AssetCreate,
    AssetDeletionResponse,
    AssetUpdate,
    AssetWithPrice,
    ExternalAssetProvider,
    ExternalAssetSearchResult,
    ExternalCryptoSearchResult,
)
from app.schemas.asset_price import CurrentPrice, PriceRefreshResponse
from app.services.coingecko import CoinGeckoService
from app.services.investment_rate_limiter import enforce_investment_rate_limit
from app.services.price_fetcher import PriceFetcher
from app.services.yahoo_finance import YahooFinanceService
from app.utilities.investment_telemetry import (
    add_investment_context,
    complete_investment_event,
    fail_investment_event,
    investment_stage,
    partial_investment_failure,
)

router = APIRouter()


@router.get("", response_model=list[Asset])
async def list_assets(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    asset_class: AssetClass | None = Query(None, description="Filter by asset class"),
    asset_type: AssetType | None = Query(None, description="Filter by asset type"),
    currency: Currency | None = Query(None, description="Filter by currency"),
    market: Market | None = Query(None, description="Filter by market"),
    is_active: bool | None = Query(True, description="Filter by active status"),
) -> Any:
    """
    List all tracked assets with optional filtering.

    Filters:
    - asset_class: EQUITIES, FIXED_INCOME, CRYPTO, FUNDS
    - asset_type: STOCK, ETF, BOND, CETES, TREASURY, CRYPTOCURRENCY, MUTUAL_FUND, INDEX_FUND
    - currency: USD, MXN
    - market: BMV, NYSE, NASDAQ, CRYPTO, OTC
    """
    add_investment_context(
        request,
        asset_class=asset_class,
        asset_type=asset_type,
        currency=currency,
        market=market,
    )
    with investment_stage(request, "database_query"):
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
    complete_investment_event(request, result_count=len(assets))
    return assets


@router.post("", response_model=Asset)
async def create_asset(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_in: AssetCreate,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Create a new asset to track.

    The asset_class is automatically inferred from asset_type if not provided.
    """
    # Check if asset already exists
    add_investment_context(
        request,
        symbol=asset_in.symbol.upper(),
        asset_type=asset_in.asset_type,
    )
    with investment_stage(request, "identity_check"):
        existing = await crud.asset.get_by_symbol(db, symbol=asset_in.symbol)
    if existing:
        fail_investment_event(request, reason="asset_already_exists")
        raise HTTPException(
            status_code=400,
            detail=f"Asset with symbol {asset_in.symbol.upper()} already exists",
        )

    # Auto-set asset_class from asset_type
    if asset_in.asset_class is None:
        asset_in.asset_class = ASSET_TYPE_TO_CLASS.get(asset_in.asset_type)

    try:
        with investment_stage(request, "database_write"):
            asset = await crud.asset.create(db, obj_in=asset_in)
    except IntegrityError:
        await db.rollback()
        fail_investment_event(request, reason="asset_identity_conflict")
        raise HTTPException(status_code=409, detail="Asset identity already exists")
    add_investment_context(request, asset_id=asset.id)
    complete_investment_event(request, asset_created=True)
    return asset


@router.get("/search", response_model=list[Asset])
async def search_assets(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    q: str = Query(..., min_length=1, max_length=100, description="Search query"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> Any:
    """
    Search assets by symbol or name.
    """
    q = q.strip()
    if not q:
        fail_investment_event(request, reason="blank_search_query", stage="validation")
        raise HTTPException(status_code=422, detail="Search query cannot be blank")
    with investment_stage(request, "database_search"):
        assets = await crud.asset.search_assets(db, query=q, skip=skip, limit=limit)
    complete_investment_event(request, result_count=len(assets))
    return assets


@router.get("/search-external", response_model=list[ExternalAssetSearchResult])
async def search_external_assets(
    request: Request,
    current_user: models.User = Depends(deps.get_current_active_user),
    q: str = Query(..., min_length=1, max_length=100, description="Search query (symbol or name)"),
) -> Any:
    """
    Search for assets from external sources (Yahoo Finance).

    Searches both USA (NYSE, NASDAQ) and Mexican (BMV) markets.
    Results include stocks and ETFs that can be added to the portfolio.
    """
    q = q.strip()
    if not q:
        fail_investment_event(request, reason="blank_search_query", stage="validation")
        raise HTTPException(status_code=422, detail="Search query cannot be blank")
    add_investment_context(request, provider="yahoo")
    with investment_stage(request, "rate_limit"):
        enforce_investment_rate_limit(f"user:{current_user.id}:search-assets", 0.5)
    with investment_stage(request, "provider_lookup"):
        results = await YahooFinanceService.search_symbol(q)

    # Filter to only include stocks and ETFs from USA and Mexico exchanges
    allowed_exchanges = {"NYQ", "NMS", "NGM", "PCX", "BTS", "MEX", "NYSE", "NASDAQ"}
    allowed_types = {"EQUITY", "ETF"}

    filtered_results = []
    for item in results:
        raw_symbol = item.get("symbol")
        raw_name = item.get("name")
        if (
            not isinstance(raw_symbol, str)
            or not raw_symbol
            or len(raw_symbol) > 128
            or (raw_name is not None and not isinstance(raw_name, str))
            or (isinstance(raw_name, str) and len(raw_name) > 255)
        ):
            continue
        exchange = item.get("exchange", "")
        quote_type = item.get("type", "")
        if not isinstance(exchange, str) or len(exchange) > 32:
            continue

        # Include if it matches our criteria or is a Mexican stock
        is_mexican = raw_symbol.endswith(".MX")
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
            symbol = raw_symbol.removesuffix(".MX")
            if not symbol or len(symbol) > 32:
                continue

            filtered_results.append(ExternalAssetSearchResult(
                provider=ExternalAssetProvider.YAHOO,
                external_id=raw_symbol,
                symbol=symbol,
                name=raw_name or symbol,
                asset_type=asset_type,
                market=market,
                currency=currency,
                country=country,
                exchange=exchange,
            ))

    complete_investment_event(request, result_count=len(filtered_results))
    return filtered_results


@router.get("/search-crypto", response_model=list[ExternalCryptoSearchResult])
async def search_crypto_assets(
    request: Request,
    current_user: models.User = Depends(deps.get_current_active_user),
    q: str = Query(..., min_length=1, max_length=100, description="Search query (symbol or name)"),
) -> Any:
    """
    Search for cryptocurrencies from CoinGecko.

    Returns cryptocurrency search results that can be added to the portfolio.
    Results include symbol, name, CoinGecko ID, and market cap rank.
    """
    q = q.strip()
    if not q:
        fail_investment_event(request, reason="blank_search_query", stage="validation")
        raise HTTPException(status_code=422, detail="Search query cannot be blank")
    add_investment_context(request, provider="coingecko")
    with investment_stage(request, "rate_limit"):
        enforce_investment_rate_limit(f"user:{current_user.id}:search-crypto", 0.5)
    with investment_stage(request, "provider_lookup"):
        results = await CoinGeckoService.search_coins(q)

    crypto_results = []
    for coin in results:
        coin_id = coin.get("coingecko_id")
        symbol = coin.get("symbol")
        name = coin.get("name")
        if (
            not isinstance(coin_id, str)
            or not coin_id
            or len(coin_id) > 128
            or not isinstance(symbol, str)
            or not symbol
            or len(symbol) > 32
            or not isinstance(name, str)
            or not name
            or len(name) > 255
        ):
            continue
        crypto_results.append(ExternalCryptoSearchResult(
            provider=ExternalAssetProvider.COINGECKO,
            external_id=coin_id,
            symbol=symbol,
            name=name,
            asset_type=AssetType.CRYPTOCURRENCY,
            market=Market.CRYPTO,
            currency=Currency.USD,
            coingecko_id=coin_id,
            market_cap_rank=coin.get("market_cap_rank"),
        ))

    complete_investment_event(request, result_count=len(crypto_results))
    return crypto_results


@router.get("/{asset_id}", response_model=AssetWithPrice)
async def get_asset(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get asset by ID with current price.
    """
    add_investment_context(request, asset_id=asset_id)
    with investment_stage(request, "database_query"):
        asset = await crud.asset.get(db, id=asset_id)
    if not asset:
        fail_investment_event(request, reason="asset_not_found")
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

    add_investment_context(request, symbol=asset.symbol)
    complete_investment_event(request, price_available=latest_price is not None)
    return response


@router.put("/{asset_id}", response_model=Asset)
async def update_asset(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_id: int = Path(..., ge=1),
    asset_in: AssetUpdate,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Update an asset.
    """
    add_investment_context(request, asset_id=asset_id)
    with investment_stage(request, "database_query"):
        asset = await crud.asset.get(db, id=asset_id)
    if not asset:
        fail_investment_event(request, reason="asset_not_found")
        raise HTTPException(status_code=404, detail="Asset not found")

    asset_in.updated_at = datetime.now(timezone.utc)
    try:
        with investment_stage(request, "database_write"):
            asset = await crud.asset.update(db, db_obj=asset, obj_in=asset_in)
    except IntegrityError:
        await db.rollback()
        fail_investment_event(request, reason="asset_identity_conflict")
        raise HTTPException(status_code=409, detail="Asset identity already exists")
    add_investment_context(request, symbol=asset.symbol)
    complete_investment_event(request)
    return asset


@router.delete("/{asset_id}", response_model=AssetDeletionResponse)
async def delete_asset(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Deactivate a global asset. Superuser only.
    """
    add_investment_context(request, asset_id=asset_id)
    with investment_stage(request, "database_query"):
        asset = await crud.asset.get(db, id=asset_id)
    if not asset:
        fail_investment_event(request, reason="asset_not_found")
        raise HTTPException(status_code=404, detail="Asset not found")

    with investment_stage(request, "database_write"):
        await crud.asset.update(db, db_obj=asset, obj_in={"is_active": False})
    add_investment_context(request, symbol=asset.symbol)
    complete_investment_event(request)
    return AssetDeletionResponse(message=f"Asset {asset.symbol} deactivated")


@router.get("/{asset_id}/price", response_model=CurrentPrice)
async def get_asset_price(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    asset_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
    refresh: bool = Query(False, description="Force refresh price from API"),
) -> Any:
    """
    Get current price for an asset.
    
    Set refresh=true to force fetching from external API.
    """
    add_investment_context(request, asset_id=asset_id)
    with investment_stage(request, "database_query"):
        asset = await crud.asset.get(db, id=asset_id)
    if not asset:
        fail_investment_event(request, reason="asset_not_found")
        raise HTTPException(status_code=404, detail="Asset not found")
    provider = None
    if asset.asset_class == AssetClass.CRYPTO:
        provider = "coingecko"
    elif asset.asset_class == AssetClass.EQUITIES:
        provider = "yahoo"
    add_investment_context(
        request,
        symbol=asset.symbol,
        asset_class=asset.asset_class,
        provider=provider,
    )

    can_refresh = crud.user.is_superuser(current_user) or await crud.holding.exists_by_owner_and_asset(
        db, owner_id=current_user.id, asset_id=asset_id
    )
    max_age_minutes = 1 if refresh else 15
    is_stale = await crud.asset_price.is_stale(
        db, asset_id=asset_id, max_age_minutes=max_age_minutes
    )

    if refresh and not can_refresh:
        fail_investment_event(request, reason="price_refresh_forbidden", stage="authorization")
        raise HTTPException(
            status_code=403,
            detail="Only asset holders or superusers can refresh this price",
        )

    if refresh and not asset.is_active:
        fail_investment_event(request, reason="asset_inactive", stage="validation")
        raise HTTPException(status_code=409, detail="Asset is inactive")

    if can_refresh and (refresh or is_stale):
        # Asset-global throttling prevents different users from repeatedly refreshing
        # the same shared price and rewriting every holder's valuation.
        with investment_stage(request, "rate_limit"):
            enforce_investment_rate_limit(f"asset:{asset_id}:refresh", 5.0)

    with investment_stage(request, "price_fetch"):
        if refresh and is_stale:
            price_data = await PriceFetcher.fetch_and_store_price(db, asset)
        else:
            price_data = await PriceFetcher.get_current_price(
                db,
                asset,
                max_age_minutes=max_age_minutes,
                allow_fetch=can_refresh,
            )

    if not price_data:
        fail_investment_event(request, reason="price_unavailable")
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch price for {asset.symbol}. This asset may require manual price entry."
        )

    latest_price = await crud.asset_price.get_latest_by_asset(db, asset_id=asset_id)

    complete_investment_event(
        request,
        refreshed=bool(refresh and is_stale),
        cache_hit=bool(not is_stale),
    )
    return CurrentPrice(
        symbol=asset.symbol,
        price=price_data.price,
        currency=price_data.currency,
        price_usd=price_data.price_usd,
        price_mxn=price_data.price_mxn,
        change=price_data.change,
        change_percent=price_data.change_percent,
        fetched_at=(
            latest_price.fetched_at if latest_price else datetime.now(timezone.utc)
        ),
    )


@router.post("/refresh-prices", response_model=PriceRefreshResponse)
async def refresh_all_prices(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    only_my_holdings: bool = Query(True, description="Only refresh assets in my portfolio"),
) -> Any:
    """
    Refresh prices for tracked assets.
    
    By default, only refreshes assets the user has holdings in.
    Set only_my_holdings=false to refresh all active assets (admin).
    """
    if not only_my_holdings and not crud.user.is_superuser(current_user):
        fail_investment_event(request, reason="global_refresh_forbidden", stage="authorization")
        raise HTTPException(
            status_code=403,
            detail="Only superusers can refresh all asset prices",
        )

    limiter_key = (
        "global:refresh-prices"
        if not only_my_holdings
        else f"user:{current_user.id}:refresh-prices"
    )
    with investment_stage(request, "rate_limit"):
        enforce_investment_rate_limit(limiter_key, 30.0)
    owner_id = current_user.id if only_my_holdings else None

    with investment_stage(request, "bulk_price_refresh"):
        updated_count, failed_symbols = await PriceFetcher.refresh_all_prices(
            db, owner_id=owner_id
        )

    if failed_symbols:
        partial_investment_failure(
            request,
            reason="prices_unavailable",
            failed_count=len(failed_symbols),
            updated_count=updated_count,
        )
    else:
        complete_investment_event(request, updated_count=updated_count, failed_count=0)

    return PriceRefreshResponse(
        message=f"Refreshed {updated_count} asset prices",
        updated_count=updated_count,
        failed_symbols=failed_symbols,
    )
