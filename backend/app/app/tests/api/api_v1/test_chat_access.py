from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api import deps
from app.api.api_v1.endpoints.chat import router
from app.core.config import settings


def test_chat_access_requires_allowlisted_uuid(monkeypatch):
    user = SimpleNamespace(uuid="user_allowed")
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "CHAT_ALLOWED_USER_UUIDS",
        "user_other, user_allowed",
    )

    assert deps.require_chat_access(user) is user


@pytest.mark.parametrize("uuid", [None, "", "user_denied", "USER_ALLOWED"])
def test_chat_access_rejects_uuid_not_in_allowlist(monkeypatch, uuid):
    user = SimpleNamespace(uuid=uuid)
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_ALLOWED_USER_UUIDS", "user_allowed")

    with pytest.raises(HTTPException) as exc_info:
        deps.require_chat_access(user)

    assert exc_info.value.status_code == 403


def test_chat_access_rejects_everyone_when_allowlist_is_empty(monkeypatch):
    user = SimpleNamespace(uuid="user_allowed")
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_ALLOWED_USER_UUIDS", "")

    with pytest.raises(HTTPException) as exc_info:
        deps.require_chat_access(user)

    assert exc_info.value.status_code == 403


def test_chat_access_preserves_global_kill_switch(monkeypatch):
    user = SimpleNamespace(uuid="user_allowed")
    monkeypatch.setattr(settings, "CHAT_ENABLED", False)
    monkeypatch.setattr(settings, "CHAT_ALLOWED_USER_UUIDS", "user_allowed")

    with pytest.raises(HTTPException) as exc_info:
        deps.require_chat_access(user)

    assert exc_info.value.status_code == 503


def test_every_chat_route_requires_uuid_access():
    chat_routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert chat_routes
    for route in chat_routes:
        assert any(
            dependency.call is deps.require_chat_access
            for dependency in route.dependant.dependencies
        ), f"{route.methods} {route.path} is missing the chat access gate"
