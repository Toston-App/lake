"""Pydantic schemas for the subscription/tier/checkout surface."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TierOut(BaseModel):
    tier: str
    label: str
    monthly_token_limit: int | None = None
    features: list[str]
    # Chat runs on the smarter Premium model.
    premium_model: bool = False
    price_hint: str | None = None
    # Whether this tier is currently purchasable (has a configured product id).
    available: bool = False


class CurrentTierOut(BaseModel):
    """The caller's tier, as shown next to their usage."""

    tier: str
    label: str
    premium_model: bool = False
    available: bool = False


class UsageOut(BaseModel):
    """Full period usage. Internal: clients get `UsageSummaryOut`."""

    period_start: datetime
    period_end: datetime | None = None
    # Counted for reporting only; messages are not limited.
    messages_used: int
    tokens_used: int
    tokens_limit: int | None = None
    # Share of tokens_limit used this period, 0-100. None when unlimited.
    percent_used: float | None = None


class UsageSummaryOut(BaseModel):
    """Usage as reported to clients: the share of the token limit used."""

    model_config = ConfigDict(from_attributes=True)

    period_start: datetime
    period_end: datetime | None = None
    percent_used: float | None = None


class SubscriptionOut(BaseModel):
    tier: str
    status: str
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    # A plan change scheduled for the next period (e.g. a downgrade).
    pending_tier: str | None = None
    pending_effective_at: datetime | None = None
    # True when the user can open the Polar customer portal (has a bound customer).
    can_manage: bool = False


class SubscriptionMeOut(BaseModel):
    subscription: SubscriptionOut
    usage: UsageSummaryOut
    tier_config: CurrentTierOut


class CheckoutRequest(BaseModel):
    tier: str  # tier1 | tier2


class CheckoutOut(BaseModel):
    url: str
    checkout_id: str


class ChangePlanOut(BaseModel):
    requested_tier: str
    effective: str  # immediate | next_period
    effective_at: datetime | None = None


class PortalOut(BaseModel):
    url: str
