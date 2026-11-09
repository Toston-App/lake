"""Per-key fixed-window rate limits, enforced as ServiceErrors.

The app-level ServiceError handler turns these into responses, so callers
need no try/except.
"""

import logging
from typing import Any

from app.services.errors import ServiceError
from app.utilities.redis import hit_rate_limit

logger = logging.getLogger(__name__)

HOUR = 3600


class RateLimitedError(ServiceError):
    """Over the limit for the current window."""

    status_code = 429

    def __init__(self, retry_after: int):
        super().__init__("Too many requests")
        self.retry_after = retry_after

    def detail(self) -> dict[str, Any]:
        return {"reason": "rate_limited", "retry_after": self.retry_after}

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


class RateLimiterUnavailableError(ServiceError):
    """The limiter is down and the limit fails closed."""

    status_code = 503

    def __init__(self):
        super().__init__("Rate limiting is temporarily unavailable")

    def detail(self) -> dict[str, Any]:
        return {"reason": "rate_limit_unavailable"}


async def enforce_rate_limit(
    key: str, limit: int, *, window_seconds: int = HOUR, fail_open: bool
) -> bool:
    """Count a hit against `limit` per window; RateLimitedError when over it.

    A limit of 0 or less disables it. Returns False when the limiter is
    unavailable (Redis down) and `fail_open`: the request goes ahead and the
    caller decides what to drop to keep it cheap. Without `fail_open` that
    raises RateLimiterUnavailableError instead.
    """
    if limit <= 0:
        return True
    try:
        hits, retry_after = await hit_rate_limit(key, window_seconds)
    except Exception as exc:
        if not fail_open:
            raise RateLimiterUnavailableError() from exc
        logger.warning("Rate limiter unavailable for %s", key, exc_info=True)
        return False
    if hits > limit:
        raise RateLimitedError(retry_after)
    return True
