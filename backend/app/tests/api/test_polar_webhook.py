from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.config import settings


async def test_webhook_rejects_oversized_body(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_WEBHOOK_MAX_BYTES", 4)

    response = await client.post(
        "/api/v1/polar/webhook",
        content=b"12345",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


async def test_duplicate_delivery_replays_handler_for_cache_repair(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api.api_v1.endpoints import polar as endpoint

    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_WEBHOOK_SECRET", "test-secret")
    event = SimpleNamespace(TYPE="ignored.test", type="ignored.test", data=None)
    monkeypatch.setattr("polar_sdk.webhooks.validate_event", lambda **_: event)
    handler = AsyncMock(return_value=None)
    monkeypatch.setattr(endpoint, "handle_polar_event", handler)
    headers = {"webhook-id": "duplicate-test", "content-type": "application/json"}

    first = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)
    second = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert handler.await_count == 2


async def test_handler_failure_rolls_back_claim_and_allows_retry(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.api_v1.endpoints import polar as endpoint

    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_WEBHOOK_SECRET", "test-secret")
    event = SimpleNamespace(TYPE="ignored.test", type="ignored.test", data=None)
    monkeypatch.setattr("polar_sdk.webhooks.validate_event", lambda **_: event)
    handler = AsyncMock(
        side_effect=[RuntimeError("entitlement cache invalidation failed"), None]
    )
    monkeypatch.setattr(endpoint, "handle_polar_event", handler)
    headers = {"webhook-id": "retry-test", "content-type": "application/json"}

    first = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)
    second = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)

    assert first.status_code == 500
    assert second.status_code == 202
    assert handler.await_count == 2


async def test_cache_failure_after_commit_is_repaired_by_duplicate_retry(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.api_v1.endpoints import polar as endpoint

    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_WEBHOOK_SECRET", "test-secret")
    event = SimpleNamespace(TYPE="subscription.active", data=None)
    monkeypatch.setattr("polar_sdk.webhooks.validate_event", lambda **_: event)
    handler = AsyncMock(return_value=123)
    invalidate = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(endpoint, "handle_polar_event", handler)
    monkeypatch.setattr(endpoint, "invalidate_entitlement", invalidate)
    headers = {"webhook-id": "cache-retry-test", "content-type": "application/json"}

    first = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)
    second = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)

    assert first.status_code == 500
    assert second.status_code == 202
    assert handler.await_count == 2
    assert invalidate.await_count == 2
