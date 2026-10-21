from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api import deps
from app.api.api_v1.endpoints.chat import router
from app.core.config import settings


@pytest.mark.parametrize("uuid", [None, "", "user_any"])
def test_chat_access_allows_every_user(monkeypatch, uuid):
    user = SimpleNamespace(uuid=uuid)
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)

    assert deps.require_chat_access(user) is user


def test_chat_access_preserves_global_kill_switch(monkeypatch):
    user = SimpleNamespace(uuid="user_any")
    monkeypatch.setattr(settings, "CHAT_ENABLED", False)

    with pytest.raises(HTTPException) as exc_info:
        deps.require_chat_access(user)

    assert exc_info.value.status_code == 503


def test_every_chat_route_requires_chat_access():
    chat_routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert chat_routes
    for route in chat_routes:
        assert any(
            dependency.call is deps.require_chat_access
            for dependency in route.dependant.dependencies
        ), f"{route.methods} {route.path} is missing the chat access gate"
