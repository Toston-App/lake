from time import monotonic

from fastapi import HTTPException


_last_calls: dict[str, float] = {}
_MAX_TRACKED_KEYS = 10_000


def enforce_investment_rate_limit(key: str, seconds: float) -> None:
    """Apply a small per-process guard to costly investment operations."""
    now = monotonic()
    if len(_last_calls) >= _MAX_TRACKED_KEYS:
        cutoff = now - 3600
        for stale_key, called_at in list(_last_calls.items()):
            if called_at < cutoff:
                _last_calls.pop(stale_key, None)
        while len(_last_calls) >= _MAX_TRACKED_KEYS:
            _last_calls.pop(next(iter(_last_calls)))

    last_call = _last_calls.get(key)
    if last_call is not None and now - last_call < seconds:
        raise HTTPException(status_code=429, detail="Too many external data requests")
    _last_calls[key] = now
