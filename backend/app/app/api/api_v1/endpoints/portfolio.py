"""
Portfolio analytics endpoints for the Investment Dashboard.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.models.asset import AssetClass, AssetType, Currency, Market
from app.models.investment_transaction import InvestmentTransaction, TransactionType
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.schemas.portfolio import (
    AllocationByAccount,
    AllocationByClass,
    AllocationByCountry,
    AllocationByCurrency,
    AllocationByMarket,
    AllocationByType,
    AllocationItem,
    PerformanceDataPoint,
    PerformancePeriod,
    PortfolioPerformance,
    PortfolioSummary,
    TopHolding,
    TopHoldingsResponse,
)
from app.services.currency_converter import CurrencyConverter, CurrencyRateUnavailable
from app.services.portfolio_performance import (
    SplitEvent,
    Valuation,
    compute_performance,
)
from app.services.portfolio_snapshot import (
    PortfolioTotals,
    combined_invested,
    compute_portfolio_totals,
    portfolio_today,
)
from app.utilities.investment_telemetry import (
    begin_investment_stage,
    complete_investment_event,
    complete_investment_stage,
    investment_stage,
)

router = APIRouter()
ZERO = Decimal("0")


async def _trusted_usd_mxn_rate() -> Decimal:
    try:
        return await CurrencyConverter.get_usd_to_mxn_rate()
    except CurrencyRateUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="A current USD/MXN rate is unavailable"
        ) from exc


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

    total_value_usd = ZERO
    total_value_mxn = ZERO
    total_invested_usd = ZERO
    total_invested_mxn = ZERO
    total_gain_loss = ZERO

    for holding in holdings:
        total_value_usd += holding.current_value_usd
        total_value_mxn += holding.current_value_mxn
        # Separate total_invested by cost_currency
        if holding.cost_currency == Currency.USD:
            total_invested_usd += holding.total_invested
        else:
            total_invested_mxn += holding.total_invested

    # Calculate combined totals (all investments converted to single currency)
    total_invested_combined_usd = total_invested_usd + (
        total_invested_mxn / usd_mxn_rate
    )
    total_invested_combined_mxn = (
        total_invested_usd * usd_mxn_rate
    ) + total_invested_mxn

    total_gain_loss = total_value_usd - total_invested_combined_usd
    # Calculate total percentage gain/loss
    total_gain_loss_pct = ZERO
    if total_invested_combined_usd > 0:
        total_gain_loss_pct = (
            (total_value_usd - total_invested_combined_usd)
            / total_invested_combined_usd
        ) * 100

    # Count unique assets
    asset_ids = {h.asset_id for h in holdings}

    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        holdings_count=len(holdings),
        assets_count=len(asset_ids),
    )
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


_PERIOD_DAYS = {
    PerformancePeriod.ONE_WEEK: 7,
    PerformancePeriod.ONE_MONTH: 30,
    PerformancePeriod.THREE_MONTHS: 90,
    PerformancePeriod.SIX_MONTHS: 182,
    PerformancePeriod.ONE_YEAR: 365,
}


def _period_start(period: PerformancePeriod, today: date) -> date | None:
    """First day of the window; None means no lower bound (ALL)."""
    if period == PerformancePeriod.ALL:
        return None
    if period == PerformancePeriod.YEAR_TO_DATE:
        return date(today.year, 1, 1)
    return today - timedelta(days=_PERIOD_DAYS[period])


def _snapshot_valuation(snapshot: PortfolioSnapshot, currency: Currency) -> Valuation:
    in_usd = currency == Currency.USD
    return Valuation(
        day=snapshot.snapshot_date,
        captured_at=snapshot.captured_at,
        value=snapshot.total_value_usd if in_usd else snapshot.total_value_mxn,
        invested=combined_invested(
            snapshot.total_invested_usd,
            snapshot.total_invested_mxn,
            snapshot.usd_mxn_rate,
            currency,
        ),
        positions={
            p.holding_id: (p.quantity, p.value_usd if in_usd else p.value_mxn)
            for p in snapshot.positions
        },
    )


def _live_valuation(
    totals: PortfolioTotals, rate: Decimal, currency: Currency, today: date
) -> Valuation:
    in_usd = currency == Currency.USD
    return Valuation(
        day=today,
        captured_at=datetime.now(timezone.utc),
        value=totals.total_value_usd if in_usd else totals.total_value_mxn,
        invested=combined_invested(
            totals.total_invested_usd, totals.total_invested_mxn, rate, currency
        ),
        positions={
            p.holding_id: (p.quantity, p.value_usd if in_usd else p.value_mxn)
            for p in totals.positions
        },
    )


@router.get("/performance", response_model=PortfolioPerformance)
async def get_portfolio_performance(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    period: PerformancePeriod = Query(PerformancePeriod.ONE_MONTH),
    currency: Currency = Query(Currency.USD),
) -> Any:
    """
    Get portfolio value over time and its time-weighted return for a period.
    """
    today = portfolio_today()
    start = _period_start(period, today)

    with investment_stage(request, "database_query"):
        snapshots = await crud.portfolio_snapshot.get_range(
            db, owner_id=current_user.id, start_date=start
        )
        if start is not None:
            # Measure from the last known value at or before the window start.
            baseline = await crud.portfolio_snapshot.get_latest_on_or_before(
                db, owner_id=current_user.id, day=start
            )
            if baseline is not None and baseline.id not in {s.id for s in snapshots}:
                snapshots.insert(0, baseline)
        # The live point replaces today's stored snapshot.
        snapshots = [s for s in snapshots if s.snapshot_date != today]
        holdings = await crud.holding.get_by_owner(
            db, owner_id=current_user.id, limit=None
        )
        split_query = select(InvestmentTransaction).where(
            InvestmentTransaction.owner_id == current_user.id,
            InvestmentTransaction.transaction_type == TransactionType.SPLIT,
        )
        if snapshots:
            split_query = split_query.where(
                InvestmentTransaction.created_at > snapshots[0].captured_at
            )
        splits = [
            SplitEvent(
                holding_id=tx.holding_id, recorded_at=tx.created_at, ratio=tx.quantity
            )
            for tx in (await db.execute(split_query)).scalars().all()
        ]

    with investment_stage(request, "fx_lookup"):
        rate = await _trusted_usd_mxn_rate()
    begin_investment_stage(request, "aggregation")

    valuations = [_snapshot_valuation(s, currency) for s in snapshots] + [
        _live_valuation(compute_portfolio_totals(holdings, rate), rate, currency, today)
    ]
    result = compute_performance(valuations, splits)

    data_points = []
    for valuation in valuations:
        gain_loss = valuation.value - valuation.invested
        gain_loss_pct = (
            gain_loss / valuation.invested * 100 if valuation.invested > 0 else ZERO
        )
        data_points.append(
            PerformanceDataPoint(
                date=valuation.day,
                value=round(valuation.value, 2),
                invested=round(valuation.invested, 2),
                gain_loss=round(gain_loss, 2),
                gain_loss_pct=round(gain_loss_pct, 2),
            )
        )

    complete_investment_stage(request, "aggregation")
    complete_investment_event(
        request,
        result_count=len(valuations),
        holdings_count=len(holdings),
    )
    first, last = valuations[0], valuations[-1]
    return PortfolioPerformance(
        period=period,
        currency=currency,
        start_date=first.day,
        end_date=last.day,
        start_value=round(first.value, 2),
        end_value=round(last.value, 2),
        net_contributions=round(result.net_contributions, 2),
        absolute_return=round(result.absolute_return, 2),
        percentage_return=round(result.percentage_return, 2),
        data_points=data_points,
    )
