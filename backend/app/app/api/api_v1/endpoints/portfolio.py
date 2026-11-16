"""
Portfolio analytics endpoints for the Investment Dashboard.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.models.asset import AssetClass, AssetType, Currency, Market
from app.schemas.portfolio import (
    AllocationByAccount,
    AllocationByClass,
    AllocationByCountry,
    AllocationByCurrency,
    AllocationByMarket,
    AllocationByType,
    AllocationItem,
    PortfolioSummary,
    TopHolding,
    TopHoldingsResponse,
)
from app.services import investments as investment_service
from app.utilities.investment_telemetry import (
    begin_investment_stage,
    complete_investment_event,
    complete_investment_stage,
    investment_stage,
)

router = APIRouter()
ZERO = investment_service.ZERO


async def _trusted_usd_mxn_rate() -> Decimal:
    # FxRateUnavailable is a ServiceError: the API reports it as a 503.
    return await investment_service.usd_mxn_rate()


@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get overall portfolio summary with total value, gain/loss, and basic metrics.
    """
    with investment_stage(request, "database_query"):
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )

    # Get exchange rate for currency conversion
    with investment_stage(request, "fx_lookup"):
        usd_mxn_rate = await _trusted_usd_mxn_rate()
    begin_investment_stage(request, "aggregation")
    summary = investment_service.summarize_holdings(holdings, usd_mxn_rate)
    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        holdings_count=summary.total_holdings,
        assets_count=summary.total_assets,
    )
    return summary


@router.get("/allocation/by-class", response_model=AllocationByClass)
async def get_allocation_by_class(
    request: Request,
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
    with investment_stage(request, "database_query"):
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )
    begin_investment_stage(request, "aggregation")

    # Group holdings by asset class
    class_totals: dict[AssetClass, dict] = defaultdict(
        lambda: {"usd": ZERO, "mxn": ZERO, "count": 0}
    )
    total_usd = ZERO
    total_mxn = ZERO

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
        data = class_totals.get(asset_class, {"usd": ZERO, "mxn": ZERO, "count": 0})
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else ZERO

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

    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        holdings_count=len(holdings),
        groups_count=len(allocations),
    )
    return AllocationByClass(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
        **breakdown,
    )


@router.get("/allocation/by-currency", response_model=AllocationByCurrency)
async def get_allocation_by_currency(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by currency exposure.

    Shows how much of the portfolio is exposed to USD vs MXN denominated assets.
    """
    with investment_stage(request, "database_query"):
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )
    begin_investment_stage(request, "aggregation")

    # Group by currency
    currency_totals: dict[Currency, dict] = defaultdict(
        lambda: {"usd": ZERO, "mxn": ZERO, "count": 0}
    )
    total_usd = ZERO
    total_mxn = ZERO

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
        data = currency_totals.get(currency, {"usd": ZERO, "mxn": ZERO, "count": 0})
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else ZERO

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

    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        holdings_count=len(holdings),
        groups_count=len(allocations),
    )
    return AllocationByCurrency(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
        **breakdown,
    )


@router.get("/allocation/by-market", response_model=AllocationByMarket)
async def get_allocation_by_market(
    request: Request,
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
    with investment_stage(request, "database_query"):
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )
    begin_investment_stage(request, "aggregation")

    market_totals: dict[Market, dict] = defaultdict(
        lambda: {"usd": ZERO, "mxn": ZERO, "count": 0}
    )
    total_usd = ZERO
    total_mxn = ZERO

    for holding in holdings:
        asset = holding.asset
        market_totals[asset.market]["usd"] += holding.current_value_usd
        market_totals[asset.market]["mxn"] += holding.current_value_mxn
        market_totals[asset.market]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn

    allocations = []
    for market in Market:
        data = market_totals.get(market, {"usd": ZERO, "mxn": ZERO, "count": 0})
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else ZERO

        allocations.append(
            AllocationItem(
                name=market.name,
                value=market.value,
                total_value_usd=round(data["usd"], 2),
                total_value_mxn=round(data["mxn"], 2),
                percentage=round(percentage, 2),
                holdings_count=data["count"],
            )
        )

    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        holdings_count=len(holdings),
        groups_count=len(allocations),
    )
    return AllocationByMarket(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
    )


@router.get("/allocation/by-type", response_model=AllocationByType)
async def get_allocation_by_type(
    request: Request,
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
    with investment_stage(request, "database_query"):
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )
    begin_investment_stage(request, "aggregation")

    type_totals: dict[AssetType, dict] = defaultdict(
        lambda: {"usd": ZERO, "mxn": ZERO, "count": 0}
    )
    total_usd = ZERO
    total_mxn = ZERO

    for holding in holdings:
        asset = holding.asset
        type_totals[asset.asset_type]["usd"] += holding.current_value_usd
        type_totals[asset.asset_type]["mxn"] += holding.current_value_mxn
        type_totals[asset.asset_type]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn

    allocations = []
    for asset_type in AssetType:
        data = type_totals.get(asset_type, {"usd": ZERO, "mxn": ZERO, "count": 0})
        if data["count"] == 0:
            continue  # Skip types with no holdings

        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else ZERO

        allocations.append(
            AllocationItem(
                name=asset_type.name.replace("_", " ").title(),
                value=asset_type.value,
                total_value_usd=round(data["usd"], 2),
                total_value_mxn=round(data["mxn"], 2),
                percentage=round(percentage, 2),
                holdings_count=data["count"],
            )
        )

    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        holdings_count=len(holdings),
        groups_count=len(allocations),
    )
    return AllocationByType(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
    )


@router.get("/allocation/by-country", response_model=AllocationByCountry)
async def get_allocation_by_country(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by country.

    Shows geographic diversification (US, MX, etc.)
    """
    with investment_stage(request, "database_query"):
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )
    begin_investment_stage(request, "aggregation")

    country_totals: dict[str, dict] = defaultdict(
        lambda: {"usd": ZERO, "mxn": ZERO, "count": 0}
    )
    total_usd = ZERO
    total_mxn = ZERO

    for holding in holdings:
        asset = holding.asset
        country = asset.country or "Unknown"
        country_totals[country]["usd"] += holding.current_value_usd
        country_totals[country]["mxn"] += holding.current_value_mxn
        country_totals[country]["count"] += 1
        total_usd += holding.current_value_usd
        total_mxn += holding.current_value_mxn

    allocations = []
    for country, data in sorted(
        country_totals.items(), key=lambda x: x[1]["usd"], reverse=True
    ):
        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else ZERO

        allocations.append(
            AllocationItem(
                name=country,
                value=country,
                total_value_usd=round(data["usd"], 2),
                total_value_mxn=round(data["mxn"], 2),
                percentage=round(percentage, 2),
                holdings_count=data["count"],
            )
        )

    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        holdings_count=len(holdings),
        groups_count=len(allocations),
    )
    return AllocationByCountry(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
    )


@router.get("/allocation/by-account", response_model=AllocationByAccount)
async def get_allocation_by_account(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get portfolio allocation breakdown by account.

    Aggregates holdings by the account they belong to.
    Each account's total holdings value is shown separately.
    """
    # Get all holdings
    with investment_stage(request, "database_query"):
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )

    # Get all accounts for name resolution
    accounts = await crud.account.get_multi_by_owner(
        db, owner_id=current_user.id, limit=None
    )
    begin_investment_stage(request, "aggregation")
    account_map = {a.id: a for a in accounts}

    # Group holdings by account
    account_holdings: dict[int, dict] = defaultdict(
        lambda: {"usd": ZERO, "mxn": ZERO, "count": 0}
    )
    total_usd = ZERO
    total_mxn = ZERO

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
        account_holdings.items(), key=lambda x: x[1]["usd"], reverse=True
    ):
        if data["usd"] == 0:
            continue

        percentage = (data["usd"] / total_usd * 100) if total_usd > 0 else ZERO

        account = account_map.get(account_id)
        name = account.name if account else f"Account {account_id}"
        color = account.color if account else "#168FFF"

        allocations.append(
            AllocationItem(
                name=name,
                color=color,
                value=str(account_id),
                total_value_usd=round(data["usd"], 2),
                total_value_mxn=round(data["mxn"], 2),
                percentage=round(percentage, 2),
                holdings_count=data["count"],
            )
        )

    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        holdings_count=len(holdings),
        groups_count=len(allocations),
    )
    return AllocationByAccount(
        total_value_usd=round(total_usd, 2),
        total_value_mxn=round(total_mxn, 2),
        allocations=allocations,
    )


@router.get("/top-holdings", response_model=TopHoldingsResponse)
async def get_top_holdings(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    limit: int = Query(10, ge=1, le=50, description="Number of top holdings to return"),
) -> Any:
    """
    Get top holdings by value.
    """
    with investment_stage(request, "database_query"):
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )
    begin_investment_stage(request, "ranking")

    # Calculate total portfolio value
    total_value_usd = sum(h.current_value_usd for h in holdings)

    # Sort by USD value descending
    sorted_holdings = sorted(holdings, key=lambda h: h.current_value_usd, reverse=True)
    top_holdings = sorted_holdings[:limit]

    result = []
    for holding in top_holdings:
        asset = holding.asset
        percentage = (
            (holding.current_value_usd / total_value_usd * 100)
            if total_value_usd > 0
            else ZERO
        )

        result.append(
            TopHolding(
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
            )
        )

    complete_investment_stage(request, "ranking")
    complete_investment_event(
        request,
        result_count=len(result),
        holdings_count=len(holdings),
    )
    return TopHoldingsResponse(
        holdings=result,
        total_shown=len(result),
        total_holdings=len(holdings),
    )
