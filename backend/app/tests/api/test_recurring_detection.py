"""API tests for recurring detection: /api/v1/recurring/detect and suggestions."""

from unittest.mock import AsyncMock

import pytest
from app.core.config import settings
from app.models import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.services.test_recurring_detection import add_series
from tests.utils import create_test_user

PREFIX = "/api/v1/recurring"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "free")
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )
    limiter = AsyncMock(return_value=(1, 3600))
    monkeypatch.setattr("app.services.rate_limits.hit_rate_limit", limiter)
    return limiter


def _accept(suggestion: dict, **overrides) -> dict:
    item = {
        "fingerprint": suggestion["fingerprint"],
        "transaction_type": suggestion["type"],
        "transaction_id": suggestion["occurrences"][0]["transaction_id"],
        "frequency": suggestion["frequency"],
        "month_days": suggestion["month_days"],
    }
    item.update(overrides)
    return item


async def test_requires_auth(unauth_client: AsyncClient):
    assert (await unauth_client.post(f"{PREFIX}/detect")).status_code == 401


async def test_free_user_scans_once_then_gets_402(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    await add_series(db_session, test_user)
    me = (await client.get("/api/v1/users/me")).json()
    assert me["recurring_detection"] == {"scans_remaining": 1}

    first = await client.post(f"{PREFIX}/detect", json={"use_ai": False})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["scans_remaining"] == 0
    [suggestion] = body["suggestions"]
    assert suggestion["merchant_name"] == "Netflix"
    assert suggestion["frequency"] == "monthly"

    second = await client.post(f"{PREFIX}/detect")
    assert second.status_code == 402
    detail = second.json()["detail"]
    assert detail["reason"] == "recurring_detection_limit"
    assert detail["upgrade_url"] == "/account"

    me = (await client.get("/api/v1/users/me")).json()
    assert me["recurring_detection"] == {"scans_remaining": 0}

    # The free user can still act on their result.
    last = await client.get(f"{PREFIX}/detect/last")
    assert last.status_code == 200
    assert last.json()["suggestions"][0]["fingerprint"] == suggestion["fingerprint"]
    accepted = await client.post(
        f"{PREFIX}/suggestions/accept", json={"items": [_accept(suggestion)]}
    )
    assert accepted.status_code == 200
    assert accepted.json()[0]["status"] == "created"
    assert (await client.get(f"{PREFIX}/detect/last")).json()["suggestions"] == []


async def test_paid_user_scans_without_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "tier1")
    await add_series(db_session, test_user)
    for _ in range(3):
        response = await client.post(f"{PREFIX}/detect")
        assert response.status_code == 200
        assert response.json()["scans_remaining"] is None
    me = (await client.get("/api/v1/users/me")).json()
    assert me["recurring_detection"] == {"scans_remaining": None}


async def test_rate_limit_returns_429(client: AsyncClient, _isolate: AsyncMock):
    _isolate.return_value = (11, 120)
    response = await client.post(f"{PREFIX}/detect")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "120"


async def test_rate_limit_fails_open_without_ai(
    client: AsyncClient,
    _isolate: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services import recurring_detection as detection

    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", True)
    calls = []
    detect = detection.detect

    async def spy(*args, **kwargs):
        calls.append(kwargs["use_ai"])
        return await detect(*args, **kwargs)

    monkeypatch.setattr(detection, "detect", spy)
    _isolate.side_effect = RuntimeError("redis down")
    assert (await client.post(f"{PREFIX}/detect")).status_code == 200
    # Without the limiter the scan still runs, but never calls the AI.
    assert calls == [False]


async def test_last_result_404_before_any_scan(client: AsyncClient):
    assert (await client.get(f"{PREFIX}/detect/last")).status_code == 404


async def test_accept_reports_errors_per_item(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    await add_series(db_session, test_user)
    other = await create_test_user(db_session, email="other@example.com")
    [theirs] = await add_series(db_session, other, count=1)
    [suggestion] = (await client.post(f"{PREFIX}/detect")).json()["suggestions"]

    response = await client.post(
        f"{PREFIX}/suggestions/accept",
        json={
            "items": [
                _accept(suggestion, fingerprint="a" * 32, transaction_id=theirs.id),
                _accept(suggestion),
            ]
        },
    )
    assert response.status_code == 200
    first, second = response.json()
    assert first["status"] == "error"
    assert first["recurring"] is None
    assert second["status"] == "created"
    assert second["recurring"]["frequency"] == "monthly"


async def test_dismiss_is_idempotent_and_can_be_undone(client: AsyncClient):
    item = {"fingerprint": "b" * 32, "label": "Gym · monthly"}
    first = await client.post(f"{PREFIX}/suggestions/dismiss", json={"items": [item]})
    assert first.json() == {"dismissed": 1}
    again = await client.post(f"{PREFIX}/suggestions/dismiss", json={"items": [item]})
    assert again.json() == {"dismissed": 0}

    url = f"{PREFIX}/suggestions/dismissals/{'b' * 32}"
    assert (await client.delete(url)).status_code == 200
    assert (await client.delete(url)).status_code == 404


async def test_invalid_fingerprint_is_rejected(client: AsyncClient):
    response = await client.post(
        f"{PREFIX}/suggestions/dismiss", json={"items": [{"fingerprint": "short"}]}
    )
    assert response.status_code == 422


async def test_used_free_scan_gets_402_even_when_rate_limited(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    _isolate: AsyncMock,
):
    await add_series(db_session, test_user)
    assert (await client.post(f"{PREFIX}/detect")).status_code == 200
    _isolate.return_value = (99, 60)
    assert (await client.post(f"{PREFIX}/detect")).status_code == 402


async def test_scan_already_running_returns_409(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    from app.services import recurring_detection as detection

    await detection.claim_scan(db_session, test_user, unlimited=False)
    response = await client.post(f"{PREFIX}/detect")
    assert response.status_code == 409


async def test_scan_whose_claim_was_taken_over_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services import recurring_detection as detection

    await add_series(db_session, test_user)
    monkeypatch.setattr(detection, "_finish_scan", AsyncMock(return_value=False))
    response = await client.post(f"{PREFIX}/detect")
    assert response.status_code == 409
    assert "not saved" in response.json()["detail"]


async def test_status_reports_a_running_scan(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    from app.services import recurring_detection as detection

    status = (await client.get(f"{PREFIX}/detect/status")).json()
    assert status == {"running": False, "last_scan_at": None, "scans_remaining": 1}

    claimed_at = await detection.claim_scan(db_session, test_user, unlimited=False)
    assert (await client.get(f"{PREFIX}/detect/status")).json()["running"] is True

    await detection.release_scan(db_session, test_user.id, claimed_at)
    assert (await client.get(f"{PREFIX}/detect/status")).json()["running"] is False


async def test_status_ignores_a_stale_claim(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    from datetime import datetime, timezone

    from app.models.recurring_detection import RecurringDetectionUsage
    from app.services import recurring_detection as detection

    await detection.claim_scan(db_session, test_user, unlimited=False)
    usage = await db_session.get(RecurringDetectionUsage, test_user.id)
    usage.claimed_at = datetime.now(timezone.utc) - detection.STALE_CLAIM * 2
    await db_session.commit()

    assert (await client.get(f"{PREFIX}/detect/status")).json()["running"] is False


async def test_cancelled_scan_releases_the_claim(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    import asyncio
    from contextlib import asynccontextmanager

    from app.services import recurring_detection as detection

    @asynccontextmanager
    async def test_session():
        yield db_session

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(detection, "_cleanup_session", test_session)
    monkeypatch.setattr(detection, "detect", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await detection.run_scan(db_session, test_user, unlimited=False)
    await asyncio.gather(*detection._cleanup_tasks)

    status = (await client.get(f"{PREFIX}/detect/status")).json()
    assert status["running"] is False
    assert status["scans_remaining"] == 1

    # Right after a cancel, another scan waits out a short cooldown.
    response = await client.post(f"{PREFIX}/detect")
    assert response.status_code == 429
    assert response.json()["detail"]["reason"] == "cancel_cooldown"
    assert 0 < int(response.headers["Retry-After"]) <= 60


async def test_accept_is_rate_limited(client: AsyncClient, _isolate: AsyncMock):
    _isolate.return_value = (61, 300)
    response = await client.post(
        f"{PREFIX}/suggestions/accept",
        json={
            "items": [
                {
                    "fingerprint": "a" * 32,
                    "transaction_type": "expense",
                    "transaction_id": 1,
                    "frequency": "monthly",
                }
            ]
        },
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "300"
    assert _isolate.await_args.args[0].startswith("recurring_accept:")


async def test_accept_rejects_a_fingerprint_of_another_series(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    await add_series(db_session, test_user)
    await add_series(db_session, test_user, description="Spotify", amount=9.99, day=20)
    netflix, spotify = sorted(
        (await client.post(f"{PREFIX}/detect")).json()["suggestions"],
        key=lambda s: s["merchant_name"],
    )
    response = await client.post(
        f"{PREFIX}/suggestions/accept",
        json={
            "items": [
                # Netflix's charge with a made-up fingerprint, and with Spotify's.
                _accept(netflix, fingerprint="a" * 32),
                _accept(netflix, fingerprint=spotify["fingerprint"]),
            ]
        },
    )
    assert [r["status"] for r in response.json()] == ["error", "error"]
    assert all("does not match" in r["error"] for r in response.json())
    # Spotify's suggestion is still there.
    last = (await client.get(f"{PREFIX}/detect/last")).json()["suggestions"]
    assert {s["fingerprint"] for s in last} == {
        netflix["fingerprint"],
        spotify["fingerprint"],
    }


async def test_accept_outside_a_series_cant_take_a_series_fingerprint(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    from tests.utils import create_test_expense

    await add_series(db_session, test_user)
    [netflix] = (await client.post(f"{PREFIX}/detect")).json()["suggestions"]
    loose = await create_test_expense(
        db_session, owner_id=test_user.id, description="Gym", amount=30
    )
    response = await client.post(
        f"{PREFIX}/suggestions/accept",
        json={"items": [_accept(netflix, transaction_id=loose.id)]},
    )
    [result] = response.json()
    assert result["status"] == "error"
    assert "does not match" in result["error"]


async def test_fingerprints_must_be_hex(client: AsyncClient):
    response = await client.post(
        f"{PREFIX}/suggestions/dismiss", json={"items": [{"fingerprint": "Z" * 32}]}
    )
    assert response.status_code == 422


async def test_dismissals_per_user_are_capped(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from app.services import recurring_detection as detection

    monkeypatch.setattr(detection, "MAX_DISMISSALS_PER_USER", 1)
    url = f"{PREFIX}/suggestions/dismiss"
    first = {"items": [{"fingerprint": "b" * 32}]}
    assert (await client.post(url, json=first)).json() == {"dismissed": 1}
    # Dismissing the same one again is still a no-op, not an error.
    assert (await client.post(url, json=first)).json() == {"dismissed": 0}
    second = await client.post(url, json={"items": [{"fingerprint": "c" * 32}]})
    assert second.status_code == 400
    assert "at most 1" in second.json()["detail"]
