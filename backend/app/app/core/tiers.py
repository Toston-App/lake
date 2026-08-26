"""Subscription tier catalog for Toston Chat.

Each tier maps to a Polar product (except `free`) and defines:

* `monthly_message_limit` / `monthly_token_limit` — usage caps enforced
  locally in the chat path by counting `chat_obs_turns` rows. `None` means
  unlimited.
* `features` — feature flags consumed by the chat agent to gate tools
  (see `ai/chat_tools.get_tool_definitions`).
* `polar_product_id` — the Polar product id this tier corresponds to.
  Set from env / Polar dashboard. Used only for the checkout flow; the
  webhook maps the *incoming* product id back to a tier via
  `tier_for_product`, so this is the source of truth for both directions.

The numbers below are dummy starting values; tune freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    free = "free"
    tier1 = "tier1"
    tier2 = "tier2"


# Feature keys consumed by the chat agent / system prompt.
FEATURE_SEARCH_WEB = "search_web"
FEATURE_CHARTS = "charts"
FEATURE_ADVANCED_ANALYTICS = "advanced_analytics"
FEATURE_BULK_OPERATIONS = "bulk_operations"
FEATURE_PRIORITY_SUPPORT = "priority_support"

ALL_FEATURES = frozenset(
    {
        FEATURE_SEARCH_WEB,
        FEATURE_CHARTS,
        FEATURE_ADVANCED_ANALYTICS,
        FEATURE_BULK_OPERATIONS,
        FEATURE_PRIORITY_SUPPORT,
    }
)


@dataclass(frozen=True)
class TierConfig:
    tier: Tier
    label: str
    # None => unlimited
    monthly_message_limit: int | None
    monthly_token_limit: int | None
    features: frozenset[str] = field(default_factory=frozenset)
    # Polar product id (set from env/dash). None for `free` and for any
    # paying tier whose product hasn't been wired up yet.
    polar_product_id: str | None = None
    # Human-friendly price hint for the pricing UI. Cosmetic only.
    price_hint: str | None = None


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
        monthly_message_limit=10,
        monthly_token_limit=20_000,
        features=frozenset(),
        polar_product_id=None,
        price_hint="$0",
    ),
    Tier.tier1: TierConfig(
        tier=Tier.tier1,
        label="Basic",
        monthly_message_limit=100,
        monthly_token_limit=1_000_000,
        features=frozenset({FEATURE_CHARTS}),
        polar_product_id=None,  # set via configure_product_ids from env
        price_hint="$4.99/mo",
    ),
    Tier.tier2: TierConfig(
        tier=Tier.tier2,
        label="Pro",
        monthly_message_limit=None,
        monthly_token_limit=None,
        features=frozenset(ALL_FEATURES),
        polar_product_id=None,
        price_hint="$14.99/mo",
    ),
}


def configure_product_ids(mapping: dict[str, str | None]) -> None:
    """Inject Polar product ids per tier (e.g. from env vars).

    `mapping` is {"tier1": "<product_id>", ...}. Unknown tiers are ignored.
    Rebuilds the internal product->tier index afterwards.
    """
    import copy

    global TIERS, _PRODUCT_INDEX
    new: dict[Tier, TierConfig] = {}
    for t, cfg in TIERS.items():
        pid = mapping.get(t.value) or mapping.get(t.name)
        if pid:
            new[t] = TierConfig(
                tier=cfg.tier,
                label=cfg.label,
                monthly_message_limit=cfg.monthly_message_limit,
                monthly_token_limit=cfg.monthly_token_limit,
                features=cfg.features,
                polar_product_id=pid,
                price_hint=cfg.price_hint,
            )
        else:
            new[t] = copy.copy(cfg)
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


def paying_tiers() -> list[TierConfig]:
    return [cfg for t, cfg in TIERS.items() if t != Tier.free]


def tier_from_value(value: str) -> Tier:
    """Parse a tier string; raises ValueError on unknown values."""
    try:
        return Tier(value)
    except ValueError:
        raise ValueError(f"Unknown tier: {value!r}") from None
