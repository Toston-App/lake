from math import ceil

from fastapi import HTTPException

from app.utilities.redis import r


async def enforce_investment_rate_limit(key: str, seconds: float) -> None:
    """Atomically throttle costly operations across workers and replicas."""
    redis_key = f"rate-limit:investments:{key}"
    try:
        acquired = await r.set(redis_key, "1", nx=True, ex=max(1, ceil(seconds)))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Investment request throttling is temporarily unavailable",
        ) from exc
    if not acquired:
        raise HTTPException(status_code=429, detail="Too many external data requests")
