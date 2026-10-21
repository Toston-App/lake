"""Paid plans only buy chat capacity, so they follow the chat allowlist."""

import pytest
from app.api import deps
from app.core.config import settings
from app.models.user import User
from httpx import AsyncClient


@pytest.mark.parametrize("path", ["/checkout", "/change-plan"])
async def test_users_without_chat_access_cannot_buy(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_ALLOWED_USER_UUIDS", "user_someone_else")

    response = await client.post(f"/api/v1/subscriptions{path}", json={"tier": "tier1"})

    assert response.status_code == 403


async def test_wildcard_opens_chat_to_users_without_uuid(
    test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_ALLOWED_USER_UUIDS", "*")

    assert test_user.uuid is None
    assert deps.require_chat_access(test_user) is test_user


async def test_allowlisted_user_reaches_checkout(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_ALLOWED_USER_UUIDS", "*")
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)

    response = await client.post(
        "/api/v1/subscriptions/checkout", json={"tier": "tier1"}
    )

    # Past the access gate; subscriptions are off in tests.
    assert response.status_code == 503
