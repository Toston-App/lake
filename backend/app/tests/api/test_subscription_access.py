"""Paid plans only buy chat capacity, so they follow the chat kill switch."""

import pytest
from app.core.config import settings
from httpx import AsyncClient


@pytest.mark.parametrize("path", ["/checkout", "/change-plan"])
async def test_users_cannot_buy_while_chat_is_disabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(settings, "CHAT_ENABLED", False)

    response = await client.post(f"/api/v1/subscriptions{path}", json={"tier": "tier1"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Chat is disabled"


async def test_user_reaches_checkout(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)

    response = await client.post(
        "/api/v1/subscriptions/checkout", json={"tier": "tier1"}
    )

    # Past the access gate; subscriptions are off in tests.
    assert response.status_code == 503
    assert response.json()["detail"] == "Subscriptions are disabled"
