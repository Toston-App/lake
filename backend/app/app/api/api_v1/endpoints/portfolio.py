"""
Portfolio analytics endpoints for the Investment Dashboard.
"""
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.models.asset import AssetClass, AssetType, Currency, Market
from app.schemas.portfolio import (
    PortfolioSummary,
    AllocationItem,
    AllocationByClass,
    AllocationByCurrency,
    AllocationByMarket,
    AllocationByType,
    AllocationByCountry,
    AllocationByAccount,
    TopHolding,
    TopHoldingsResponse,
)
from app.services.currency_converter import CurrencyConverter

router = APIRouter()


@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get overall portfolio summary with total value, gain/loss, and basic metrics.
    """
    holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id)
    
    # Get exchange rate for currency conversion
    usd_mxn_rate = await CurrencyConverter.get_usd_to_mxn_rate()
    
    total_value_usd = 0.0
    total_value_mxn = 0.0
    total_invested_usd = 0.0
    total_invested_mxn = 0.0
    total_gain_loss = 0.0

    for holding in holdings:
        total_value_usd += holding.current_value_usd
        total_value_mxn += holding.current_value_mxn
        # Separate total_invested by cost_currency
        if holding.cost_currency == Currency.USD:
            total_invested_usd += holding.total_invested
        else:
            total_invested_mxn += holding.total_invested

    # Calculate combined totals (all investments converted to single currency)
    total_invested_combined_usd = total_invested_usd + (total_invested_mxn / usd_mxn_rate)
    total_invested_combined_mxn = (total_invested_usd * usd_mxn_rate) + total_invested_mxn

    total_gain_loss = total_value_usd - total_invested_combined_usd
    # Calculate total percentage gain/loss
    total_gain_loss_pct = 0.0
    if total_invested_combined_usd > 0:
        total_gain_loss_pct = ((total_value_usd - total_invested_combined_usd) / total_invested_combined_usd ) * 100

    # Count unique assets
    asset_ids = set(h.asset_id for h in holdings)

    return PortfolioSummary(
        total_value_usd=round(total_value_usd, 2),
        total_value_mxn=round(total_value_mxn, 2),
        total_invested_usd=round(total_invested_usd, 2),
        total_invested_mxn=round(total_invested_mxn, 2),
        total_invested_combined_usd=round(total_invested_combined_usd, 2),
        total_invested_combined_mxn=round(total_invested_combined_mxn, 2),
        total_gain_loss=round(total_gain_loss, 2),
        total_gain_loss_pct=round(total_gain_loss_pct, 2),
        total_holdings=len(holdings),
        total_assets=len(asset_ids),
        last_updated=datetime.now(timezone.utc),
    )


@router.get("/allocation/by-class", response_model=AllocationByClass)
async def get_allocation_by_class(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by asset class.
    
    Returns allocation percentages for:
    - Equities (stocks, ETFs)
    - Fixed Income (bonds, CETES, treasuries)
    - Crypto (cryptocurrencies)
    - Funds (mutual funds, index funds)
    """
    holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id)
    
    # Group holdings by asset class
    class_totals: dict[AssetClass, dict] = defaultdict(
        lambda: {"usd": 0.0, "mxn": 0.0, "count": 0}
    )
    total_usd = 0.0
    total_mxn = 0.0
    
    for holding in holdings:
        asset = holding.asset
        class_totals[asset.asset_class]["usd"] += holding.current_value_usd
        class_totals[asset.asset_class]["mxn"] += holding.current_value_mxn
        class_totals[asset.asset_class]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn
    
    # Build allocation items
    allocations = []
    breakdown = {}
    
    for asset_class in AssetClass:
        data = class_totals.get(asset_class, {"usd": 0.0, "mxn": 0.0, "count": 0})
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else 0.0
        
        item = AllocationItem(
            name=asset_class.name.replace("_", " ").title(),
            value=asset_class.value,
            total_value_usd=round(data["usd"], 2),
            total_value_mxn=round(data["mxn"], 2),
            percentage=round(percentage, 2),
            holdings_count=data["count"],
        )
        allocations.append(item)
        
        # Map to breakdown fields
        if asset_class == AssetClass.EQUITIES:
            breakdown["equities"] = item
        elif asset_class == AssetClass.FIXED_INCOME:
            breakdown["fixed_income"] = item
        elif asset_class == AssetClass.CRYPTO:
            breakdown["crypto"] = item
        elif asset_class == AssetClass.FUNDS:
            breakdown["funds"] = item
    
    return AllocationByClass(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
        **breakdown,
    )


@router.get("/allocation/by-currency", response_model=AllocationByCurrency)
async def get_allocation_by_currency(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by currency exposure.
    
    Shows how much of the portfolio is exposed to USD vs MXN denominated assets.
    """
    holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id)
    
    # Group by currency
    currency_totals: dict[Currency, dict] = defaultdict(
        lambda: {"usd": 0.0, "mxn": 0.0, "count": 0}
    )
    total_usd = 0.0
    total_mxn = 0.0
    
    for holding in holdings:
        asset = holding.asset
        currency_totals[asset.currency]["usd"] += holding.current_value_usd
        currency_totals[asset.currency]["mxn"] += holding.current_value_mxn
        currency_totals[asset.currency]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn
    
    allocations = []
    breakdown = {}
    
    for currency in Currency:
        data = currency_totals.get(currency, {"usd": 0.0, "mxn": 0.0, "count": 0})
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else 0.0
        
        item = AllocationItem(
            name=f"{currency.value} Assets",
            value=currency.value,
            total_value_usd=round(data["usd"], 2),
            total_value_mxn=round(data["mxn"], 2),
            percentage=round(percentage, 2),
            holdings_count=data["count"],
        )
        allocations.append(item)
        
        if currency == Currency.USD:
            breakdown["usd_exposure"] = item
        elif currency == Currency.MXN:
            breakdown["mxn_exposure"] = item
    
    return AllocationByCurrency(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
        **breakdown,
    )


@router.get("/allocation/by-market", response_model=AllocationByMarket)
async def get_allocation_by_market(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by market.
    
    Shows distribution across:
    - BMV (Mexican Stock Exchange)
    - NYSE (New York Stock Exchange)
    - NASDAQ
    - CRYPTO (Cryptocurrency exchanges)
    - OTC (Over-the-counter: bonds, CETES, mutual funds)
    """
    holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id)
    
    market_totals: dict[Market, dict] = defaultdict(
        lambda: {"usd": 0.0, "mxn": 0.0, "count": 0}
    )
    total_usd = 0.0
    total_mxn = 0.0
    
    for holding in holdings:
        asset = holding.asset
        market_totals[asset.market]["usd"] += holding.current_value_usd
        market_totals[asset.market]["mxn"] += holding.current_value_mxn
        market_totals[asset.market]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn
    
    allocations = []
    for market in Market:
        data = market_totals.get(market, {"usd": 0.0, "mxn": 0.0, "count": 0})
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else 0.0
        
        allocations.append(AllocationItem(
            name=market.name,
            value=market.value,
            total_value_usd=round(data["usd"], 2),
            total_value_mxn=round(data["mxn"], 2),
            percentage=round(percentage, 2),
            holdings_count=data["count"],
        ))
    
    return AllocationByMarket(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
    )


@router.get("/allocation/by-type", response_model=AllocationByType)
async def get_allocation_by_type(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by specific asset type.
    
    More granular than by-class, showing:
    - Stocks, ETFs
    - Bonds, CETES, Treasuries
    - Cryptocurrencies
    - Mutual Funds, Index Funds
    """
    holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id)
    
    type_totals: dict[AssetType, dict] = defaultdict(
        lambda: {"usd": 0.0, "mxn": 0.0, "count": 0}
    )
    total_usd = 0.0
    total_mxn = 0.0
    
    for holding in holdings:
        asset = holding.asset
        type_totals[asset.asset_type]["usd"] += holding.current_value_usd
        type_totals[asset.asset_type]["mxn"] += holding.current_value_mxn
        type_totals[asset.asset_type]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn
    
    allocations = []
    for asset_type in AssetType:
        data = type_totals.get(asset_type, {"usd": 0.0, "mxn": 0.0, "count": 0})
        if data["count"] == 0:
            continue  # Skip types with no holdings
        
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else 0.0
        
        allocations.append(AllocationItem(
            name=asset_type.name.replace("_", " ").title(),
            value=asset_type.value,
            total_value_usd=round(data["usd"], 2),
            total_value_mxn=round(data["mxn"], 2),
            percentage=round(percentage, 2),
            holdings_count=data["count"],
        ))
    
    return AllocationByType(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
    )


@router.get("/allocation/by-country", response_model=AllocationByCountry)
async def get_allocation_by_country(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by country.
    
    Shows geographic diversification (US, MX, etc.)
    """
    holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id)
    
    country_totals: dict[str, dict] = defaultdict(
        lambda: {"usd": 0.0, "mxn": 0.0, "count": 0}
    )
    total_usd = 0.0
    total_mxn = 0.0
    
    for holding in holdings:
        asset = holding.asset
        country = asset.country or "Unknown"
        country_totals[country]["usd"] += holding.current_value_usd
        country_totals[country]["mxn"] += holding.current_value_mxn
        country_totals[country]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn
    
    allocations = []
    for country, data in sorted(country_totals.items(), key=lambda x: x[1]["usd"], reverse=True):
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else 0.0
        
        allocations.append(AllocationItem(
            name=country,
            value=country,
            total_value_usd=round(data["usd"], 2),
            total_value_mxn=round(data["mxn"], 2),
            percentage=round(percentage, 2),
            holdings_count=data["count"],
        ))
    
    return AllocationByCountry(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
    )


@router.get("/allocation/by-account", response_model=AllocationByAccount)
async def get_allocation_by_account(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by account.
    
    Aggregates holdings by the account they belong to.
    Each account's total holdings value is shown separately.
    """
    # Get all holdings
    holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id)
    
    # Get all accounts for name resolution
    accounts = await crud.account.get_multi_by_owner(db, owner_id=current_user.id)
    account_map = {a.id: a for a in accounts}
    
    # Group holdings by account
    account_holdings: dict[int, dict] = defaultdict(
        lambda: {"usd": 0.0, "mxn": 0.0, "count": 0}
    )
    total_usd = 0.0
    total_mxn = 0.0
    
    for holding in holdings:
        account_id = holding.account_id
        account_holdings[account_id]["usd"] += holding.current_value_usd
        account_holdings[account_id]["mxn"] += holding.current_value_mxn
        account_holdings[account_id]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn
    
    # Build allocation items
    allocations = []
    for account_id, data in sorted(
        account_holdings.items(),
        key=lambda x: x[1]["usd"],
        reverse=True
    ):
        if data["usd"] == 0:
            continue
        
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else 0.0
        
        account = account_map.get(account_id)
        name = account.name if account else f"Account {account_id}"
        
        allocations.append(AllocationItem(
            name=name,
            value=str(account_id),
            total_value_usd=round(data["usd"], 2),
            total_value_mxn=round(data["mxn"], 2),
            percentage=round(percentage, 2),
            holdings_count=data["count"],
        ))
    
    return AllocationByAccount(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
    )


@router.get("/top-holdings", response_model=TopHoldingsResponse)
async def get_top_holdings(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    limit: int = Query(10, ge=1, le=50, description="Number of top holdings to return"),
) -> Any:
    """
    Get top holdings by value.
    """
    holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id)
    
    # Calculate total portfolio value
    total_value_usd = sum(h.current_value_usd for h in holdings)
    
    # Sort by USD value descending
    sorted_holdings = sorted(holdings, key=lambda h: h.current_value_usd, reverse=True)
    top_holdings = sorted_holdings[:limit]
    
    result = []
    for holding in top_holdings:
        asset = holding.asset
        percentage = (holding.current_value_usd / total_value_usd * 100) if total_value_usd > 0 else 0.0
        
        result.append(TopHolding(
            symbol=asset.symbol,
            name=asset.name,
            asset_class=asset.asset_class,
            asset_type=asset.asset_type,
            quantity=holding.quantity,
            current_value_usd=round(holding.current_value_usd, 2),
            current_value_mxn=round(holding.current_value_mxn, 2),
            percentage_of_portfolio=round(percentage, 2),
            gain_loss=round(holding.unrealized_gain_loss, 2),
            gain_loss_pct=round(holding.unrealized_gain_loss_pct, 2),
        ))
    
    return TopHoldingsResponse(
        holdings=result,
        total_shown=len(result),
        total_holdings=len(holdings),
    )

