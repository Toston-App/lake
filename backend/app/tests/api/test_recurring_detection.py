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
    monkeypatch.setattr("app.api.api_v1.endpoints.recurring.hit_rate_limit", limiter)
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


async def test_rate_limit_fails_open(client: AsyncClient, _isolate: AsyncMock):
    _isolate.side_effect = RuntimeError("redis down")
    assert (await client.post(f"{PREFIX}/detect")).status_code == 200


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
