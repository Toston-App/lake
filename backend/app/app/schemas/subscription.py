"""Pydantic schemas for the subscription/tier/checkout surface."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TierOut(BaseModel):
    tier: str
    label: str
    monthly_message_limit: Optional[int] = None
    monthly_token_limit: Optional[int] = None
    features: list[str]
    price_hint: Optional[str] = None
    # Whether this tier is currently purchasable (has a configured product id).
    available: bool = False


class UsageOut(BaseModel):
    period_start: datetime
    period_end: Optional[datetime] = None
    messages_used: int
    messages_limit: Optional[int] = None
    tokens_used: int
    tokens_limit: Optional[int] = None
    remaining: Optional[int] = None  # None when unlimited


class SubscriptionOut(BaseModel):
    tier: str
    status: str
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    # True when the user can open the Polar customer portal (has a bound customer).
    can_manage: bool = False

    class Config:
        orm_mode = True


class SubscriptionMeOut(BaseModel):
    subscription: SubscriptionOut
    usage: UsageOut
    tier_config: TierOut


class CheckoutRequest(BaseModel):
    tier: str  # tier1 | tier2 | tier3


class CheckoutOut(BaseModel):
    url: str
    checkout_id: str


class PortalOut(BaseModel):
    url: str
