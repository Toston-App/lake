from datetime import datetime, timedelta, timezone

import pytest
from app.core.tiers import Tier
from app.services.entitlements import (
    effective_tier_from_payload,
    has_paid_entitlement,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("status", "cancel_at_period_end", "ends_at", "ended_at", "expected"),
    [
        ("active", False, None, None, True),
        ("trialing", False, None, None, True),
        ("past_due", False, None, None, False),
        ("active", True, NOW + timedelta(seconds=1), None, True),
        ("active", True, NOW, None, False),
        ("active", True, NOW - timedelta(seconds=1), None, False),
        ("active", True, None, None, False),
        ("canceled", True, NOW + timedelta(seconds=1), None, True),
        ("canceled", True, None, None, False),
        ("revoked", False, NOW + timedelta(days=1), None, False),
        ("unpaid", False, NOW + timedelta(days=1), None, False),
        ("unknown", False, None, None, False),
        ("active", False, None, NOW, False),
    ],
)
def test_paid_entitlement_lifecycle_boundaries(
    status: str,
    cancel_at_period_end: bool,
    ends_at: datetime | None,
    ended_at: datetime | None,
    expected: bool,
) -> None:
    assert (
        has_paid_entitlement(
            status=status,
            cancel_at_period_end=cancel_at_period_end,
            ends_at=ends_at,
            ended_at=ended_at,
            now=NOW,
        )
        is expected
    )


def test_paid_entitlement_treats_naive_timestamps_as_utc() -> None:
    assert not has_paid_entitlement(
        status="active",
        cancel_at_period_end=True,
        ends_at=NOW.replace(tzinfo=None),
        ended_at=None,
        now=NOW,
    )


def test_cached_scheduled_cancellation_expires_at_boundary() -> None:
    payload: dict[str, object] = {
        "lifecycle_version": 1,
        "tier": "tier2",
        "status": "active",
        "cancel_at_period_end": True,
        "ends_at": NOW.isoformat(),
        "ended_at": None,
    }

    assert effective_tier_from_payload(payload, now=NOW) == Tier.free


def test_legacy_cache_payload_is_reloaded() -> None:
    assert (
        effective_tier_from_payload({"tier": "tier2", "status": "active"}, now=NOW)
        is None
    )
