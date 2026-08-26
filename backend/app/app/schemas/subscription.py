"""Pydantic schemas for the subscription/tier/checkout surface."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TierOut(BaseModel):
    tier: str
    label: str
    monthly_message_limit: int | None = None
    monthly_token_limit: int | None = None
    features: list[str]
    price_hint: str | None = None
    # Whether this tier is currently purchasable (has a configured product id).
    available: bool = False


class UsageOut(BaseModel):
    period_start: datetime
    period_end: datetime | None = None
    messages_used: int
    messages_limit: int | None = None
    tokens_used: int
    tokens_limit: int | None = None
    remaining: int | None = None  # None when unlimited


class SubscriptionOut(BaseModel):
    tier: str
    status: str
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    ends_at: datetime | None = None
    ended_at: datetime | None = None
    pending_tier: str | None = None
    pending_effective_at: datetime | None = None
    # True when the user can open the Polar customer portal (has a bound customer).
    can_manage: bool = False

    class Config:
        from_attributes = True


class SubscriptionMeOut(BaseModel):
    subscription: SubscriptionOut
    usage: UsageOut
    tier_config: TierOut


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
