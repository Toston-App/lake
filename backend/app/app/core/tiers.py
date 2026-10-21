"""Subscription tier catalog for Toston Chat.

Each tier maps to a Polar product (except `free`) and defines:

* `monthly_token_limit` — the chat usage cap, enforced locally in the chat
  path by summing `chat_obs_turns` tokens. `None` means unlimited. Messages
  are counted too, but never limited.
* `premium_model` — whether chat runs on `CHAT_PREMIUM_MODEL` instead of
  `CHAT_MODEL`. Every tier gets the same chat tools.
* `features` — non-chat feature flags (investments).
* `polar_product_id` — the Polar product id this tier corresponds to.
  Set from env / Polar dashboard. Used only for the checkout flow; the
  webhook maps the *incoming* product id back to a tier via
  `tier_for_product`, so this is the source of truth for both directions.

The numbers below are dummy starting values; tune freely.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    free = "free"
    tier1 = "tier1"
    tier2 = "tier2"


# Investment portfolio endpoints (/api/v1/investments/**). Every paid tier.
FEATURE_INVESTMENTS = "investments"

ALL_FEATURES = frozenset({FEATURE_INVESTMENTS})


@dataclass(frozen=True)
class TierConfig:
    tier: Tier
    label: str
    # None => unlimited
    monthly_token_limit: int | None
    features: frozenset[str] = field(default_factory=frozenset)
    # Chat runs on CHAT_PREMIUM_MODEL instead of CHAT_MODEL.
    premium_model: bool = False
    # Polar product id (set from env/dash). None for `free` and for any
    # paying tier whose product hasn't been wired up yet.
    polar_product_id: str | None = None
    # Human-friendly price hint for the pricing UI. Cosmetic only.
    price_hint: str | None = None
    # Catalog list price used by the admin P&L dashboard. Not Polar invoices.
    monthly_price_usd: float = 0.0


# Map of product id -> tier, rebuilt whenever product ids are configured.
def _product_index() -> dict[str, Tier]:
    out: dict[str, Tier] = {}
    for cfg in TIERS.values():
        if cfg.polar_product_id:
            out[cfg.polar_product_id] = cfg.tier
    return out


# Module-level product index, refreshed by `configure_product_ids`.
_PRODUCT_INDEX: dict[str, Tier] = {}


TIERS: dict[Tier, TierConfig] = {
    Tier.free: TierConfig(
        tier=Tier.free,
        label="Free",
        monthly_token_limit=20_000,
        features=frozenset(),
        polar_product_id=None,
        price_hint="$0",
        monthly_price_usd=0.0,
    ),
    Tier.tier1: TierConfig(
        tier=Tier.tier1,
        label="Basic",
        monthly_token_limit=10_000_000,
        features=frozenset({FEATURE_INVESTMENTS}),
        polar_product_id=None,  # set via configure_product_ids from env
        price_hint="$4.99/mo",
        monthly_price_usd=4.99,
    ),
    Tier.tier2: TierConfig(
        tier=Tier.tier2,
        label="Premium",
        monthly_token_limit=100_000_000,
        features=frozenset(ALL_FEATURES),
        premium_model=True,
        polar_product_id=None,
        price_hint="$14.99/mo",
        monthly_price_usd=14.99,
    ),
}


def configure_product_ids(mapping: dict[str, str | None]) -> None:
    """Inject Polar product ids per tier (e.g. from env vars).

    `mapping` is {"tier1": "<product_id>", ...}. Unknown tiers are ignored.
    Rebuilds the internal product->tier index afterwards.
    """
    global TIERS, _PRODUCT_INDEX
    new: dict[Tier, TierConfig] = {}
    for t, cfg in TIERS.items():
        pid = mapping.get(t.value) or mapping.get(t.name)
        new[t] = dataclasses.replace(cfg, polar_product_id=pid) if pid else cfg
    TIERS = new
    _PRODUCT_INDEX = _product_index()


def tier_for_product(product_id: str | None) -> Tier:
    """Reverse-lookup a tier from a Polar product id. Defaults to free."""
    if not product_id:
        return Tier.free
    return _PRODUCT_INDEX.get(product_id, Tier.free)


def get_tier_config(tier: Tier) -> TierConfig:
    return TIERS[tier]


def has_feature(tier: Tier, feature: str) -> bool:
    return feature in TIERS[tier].features


def chat_model_for_tier(tier: Tier) -> str:
    """OpenRouter model id the chat agent uses for this tier."""
    from app.core.config import settings

    default = settings.CHAT_MODEL or settings.OPENROUTER_MODEL
    if TIERS[tier].premium_model:
        return settings.CHAT_PREMIUM_MODEL or default
    return default


def paying_tiers() -> list[TierConfig]:
    return [cfg for t, cfg in TIERS.items() if t != Tier.free]


def tier_from_value(value: str) -> Tier:
    """Parse a tier string; raises ValueError on unknown values."""
    try:
        return Tier(value)
    except ValueError:
        raise ValueError(f"Unknown tier: {value!r}") from None
