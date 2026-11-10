"""Who may call MCP tools, and how often: plan requirement and rate limits."""

from __future__ import annotations

from app import models
from app.core.config import settings
from app.core.tiers import Tier
from app.services.errors import ServiceError
from app.services.rate_limits import enforce_rate_limit


class McpPaidTierRequired(ServiceError):
    """The user's plan does not include MCP access."""

    status_code = 402

    def __init__(self) -> None:
        super().__init__("MCP access requires a paid plan")


async def enforce_mcp_limits(user: models.User, tier: Tier) -> None:
    """Apply the optional paid-plan requirement, then per-user MCP rate limits.

    The plan check comes first so a rejected free user does not burn quota.
    """
    if settings.MCP_REQUIRES_PAID and tier == Tier.free:
        raise McpPaidTierRequired()
    windows = (
        (60, settings.MCP_RATE_LIMIT_PER_MINUTE),
        (86_400, settings.MCP_RATE_LIMIT_PER_DAY),
    )
    for window_seconds, limit in windows:
        await enforce_rate_limit(
            f"mcp:{user.id}", limit, window_seconds=window_seconds, fail_open=False
        )
