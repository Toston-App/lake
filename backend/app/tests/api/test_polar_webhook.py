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


async def test_duplicate_delivery_runs_handler_once(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api.api_v1.endpoints import polar as endpoint

    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_WEBHOOK_SECRET", "test-secret")
    event = SimpleNamespace(TYPE="ignored.test", type="ignored.test", data=None)
    monkeypatch.setattr("polar_sdk.webhooks.validate_event", lambda **_: event)
    handler = AsyncMock()
    monkeypatch.setattr(endpoint, "handle_polar_event", handler)
    headers = {"webhook-id": "duplicate-test", "content-type": "application/json"}

    first = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)
    second = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    handler.assert_awaited_once()


async def test_handler_failure_rolls_back_claim_and_allows_retry(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.api_v1.endpoints import polar as endpoint

    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_WEBHOOK_SECRET", "test-secret")
    event = SimpleNamespace(TYPE="ignored.test", type="ignored.test", data=None)
    monkeypatch.setattr("polar_sdk.webhooks.validate_event", lambda **_: event)
    handler = AsyncMock(side_effect=[RuntimeError("temporary failure"), None])
    monkeypatch.setattr(endpoint, "handle_polar_event", handler)
    headers = {"webhook-id": "retry-test", "content-type": "application/json"}

    first = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)
    second = await client.post("/api/v1/polar/webhook", content=b"{}", headers=headers)

    assert first.status_code == 500
    assert second.status_code == 202
    assert handler.await_count == 2
