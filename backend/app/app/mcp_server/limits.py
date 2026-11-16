"""Who may call MCP tools, and how often: plan requirement, monthly cap, rate limits."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.core.config import settings
from app.core.tiers import TIERS, Tier
from app.services.errors import ServiceError
from app.services.mcp_audit import calls_this_month
from app.services.rate_limits import enforce_rate_limit


class McpPaidTierRequired(ServiceError):
    """The user's plan does not include MCP access."""

    status_code = 402
    reason = "paid_plan_required"

    def __init__(self) -> None:
        super().__init__("MCP access requires a paid plan")


class McpMonthlyLimitReached(ServiceError):
    """The user's plan allows no more MCP calls this month."""

    status_code = 402
    reason = "monthly_limit"

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"Your plan includes {limit} AI app calls a month and they're used "
            "up. Upgrade or wait until next month."
        )


async def enforce_mcp_limits(db: AsyncSession, user: models.User, tier: Tier) -> None:
    """Apply the optional paid-plan requirement, the plan's monthly cap, then
    per-user MCP rate limits.

    The plan checks come first so a rejected user does not burn rate quota.
    """
    if settings.MCP_REQUIRES_PAID and tier == Tier.free:
        raise McpPaidTierRequired()
    limit = TIERS[tier].mcp_monthly_call_limit
    if limit is not None and await calls_this_month(db, user.id) >= limit:
        raise McpMonthlyLimitReached(limit)
    windows = (
        (60, settings.MCP_RATE_LIMIT_PER_MINUTE),
        (86_400, settings.MCP_RATE_LIMIT_PER_DAY),
    )
    for window_seconds, limit_per_window in windows:
        await enforce_rate_limit(
            f"mcp:{user.id}",
            limit_per_window,
            window_seconds=window_seconds,
            fail_open=False,
        )
