"""Investment reads shared by the REST endpoints and the chat/MCP tools.

Who may use investments, the portfolio totals and how a holding or an
investment transaction is presented with its asset. The REST endpoints keep
their telemetry stages around these calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.core.config import settings
from app.core.tiers import FEATURE_INVESTMENTS, has_feature
from app.crud import crud_subscription
from app.models.asset import Currency
from app.schemas.holding import HoldingWithAsset
from app.schemas.investment_transaction import InvestmentTransactionWithAsset
from app.schemas.portfolio import PortfolioSummary
from app.services.currency_converter import CurrencyConverter, CurrencyRateUnavailable
from app.services.entitlements import get_user_tier
from app.services.errors import PermissionDeniedError, ServiceError
from app.services.profile import currency_of

ZERO = Decimal("0")


class InvestmentsDisabled(PermissionDeniedError):
    reason = "feature_access_denied"

    def __init__(self) -> None:
        super().__init__("Investments feature is not enabled")


class InvestmentsSubscriptionRequired(ServiceError):
    status_code = 402
    reason = "subscription_required"

    def __init__(self) -> None:
        super().__init__("Investments require an active paid subscription")


class InvestmentsReadOnly(ServiceError):
    status_code = 402
    reason = "subscription_inactive"

    def __init__(self) -> None:
        super().__init__(
            "Your subscription is inactive: investments are read-only until you renew"
        )


async def investment_access(db: AsyncSession, user: models.User) -> bool:
    """Whether the user may use investments; returns True when read-only.

    Any paid tier or a superuser has full access. A user whose subscription
    lapsed keeps read-only access to see their portfolio. Raises
    InvestmentsDisabled or InvestmentsSubscriptionRequired otherwise.
    """
    if not settings.INVESTMENTS_ENABLED:
        raise InvestmentsDisabled()
    if crud.user.is_superuser(user):
        return False
    tier, _ = await get_user_tier(db, user.id)
    if has_feature(tier, FEATURE_INVESTMENTS):
        return False
    subscription = await crud_subscription.get_by_user(db, user.id)
    if subscription is None or not subscription.polar_subscription_id:
        raise InvestmentsSubscriptionRequired()
    return True


class FxRateUnavailable(ServiceError):
    status_code = 503
    reason = "fx_unavailable"

    def __init__(self) -> None:
        super().__init__("A current USD/MXN rate is unavailable")


async def usd_mxn_rate() -> Decimal:
    """The USD to MXN rate portfolio totals use; 503 when there is no fresh one."""
    try:
        return await CurrencyConverter.get_usd_to_mxn_rate()
    except CurrencyRateUnavailable:
        raise FxRateUnavailable() from None


# The portfolio summary field valued in each currency it can be added to.
_PORTFOLIO_VALUE_FIELD = {"USD": "total_value_usd", "MXN": "total_value_mxn"}


async def portfolio_with_net_worth(db: AsyncSession, user: models.User) -> dict:
    """The portfolio summary, the cash balance and, when possible, net worth.

    Net worth is only given when the cash balance is in a currency the
    portfolio is also valued in (USD or MXN); otherwise the two can't be
    added without a rate.
    """
    holdings = await crud.holding.get_by_owner(db, owner_id=user.id, limit=None)
    summary = summarize_holdings(holdings, await usd_mxn_rate()).model_dump(mode="json")
    currency = currency_of(user)
    cash = round(float(user.balance_total or 0), 2)
    value_field = _PORTFOLIO_VALUE_FIELD.get(currency or "")
    summary["cash_balance"] = {"amount": cash, "currency": currency}
    summary["net_worth"] = (
        {"amount": round(cash + float(summary[value_field]), 2), "currency": currency}
        if value_field
        else None
    )
    return summary


def summarize_holdings(holdings: list[Any], usd_mxn_rate: Decimal) -> PortfolioSummary:
    """Portfolio totals in USD and MXN, and the gain or loss on what was invested."""
    total_value_usd = ZERO
    total_value_mxn = ZERO
    total_invested_usd = ZERO
    total_invested_mxn = ZERO
    for holding in holdings:
        total_value_usd += holding.current_value_usd
        total_value_mxn += holding.current_value_mxn
        # Separate total_invested by cost_currency
        if holding.cost_currency == Currency.USD:
            total_invested_usd += holding.total_invested
        else:
            total_invested_mxn += holding.total_invested

    # Combined totals: all investments converted to a single currency.
    total_invested_combined_usd = total_invested_usd + (
        total_invested_mxn / usd_mxn_rate
    )
    total_invested_combined_mxn = (
        total_invested_usd * usd_mxn_rate
    ) + total_invested_mxn

    total_gain_loss = total_value_usd - total_invested_combined_usd
    total_gain_loss_pct = ZERO
    if total_invested_combined_usd > 0:
        total_gain_loss_pct = (
            (total_value_usd - total_invested_combined_usd)
            / total_invested_combined_usd
        ) * 100

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
        total_assets=len({h.asset_id for h in holdings}),
        last_updated=datetime.now(timezone.utc),
    )


def holding_with_asset(holding: Any, latest_price: Any | None) -> HoldingWithAsset:
    asset = holding.asset
    data = HoldingWithAsset(
        id=holding.id,
        owner_id=holding.owner_id,
        account_id=holding.account_id,
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
        data.current_price = latest_price.price
        data.price_change = latest_price.change
        data.price_change_percent = latest_price.change_percent
    return data


async def holdings_with_assets(
    db: AsyncSession, holdings: list[Any]
) -> list[HoldingWithAsset]:
    latest_prices = await crud.asset_price.get_latest_prices(
        db, asset_ids=[holding.asset_id for holding in holdings]
    )
    return [holding_with_asset(h, latest_prices.get(h.asset_id)) for h in holdings]


def transaction_with_asset(tx: Any) -> InvestmentTransactionWithAsset:
    # The holding and asset are eagerly loaded by the CRUD queries.
    holding = tx.holding
    asset = holding.asset if holding else None
    return InvestmentTransactionWithAsset(
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
        affects_cash_balance=tx.affects_cash_balance,
        exchange_rate_to_usd=tx.exchange_rate_to_usd,
        exchange_rate_to_mxn=tx.exchange_rate_to_mxn,
        notes=tx.notes,
        executed_at=tx.executed_at,
        symbol=asset.symbol if asset else None,
        asset_name=asset.name if asset else None,
    )
