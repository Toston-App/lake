"""HTTP-level tests for the /mcp endpoint and OAuth discovery routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.config import settings
from app.core.tiers import Tier
from app.main import app
from app.mcp_server import auth as mcp_auth
from app.mcp_server import http as mcp_http
from app.mcp_server import server as mcp_server
from app.mcp_server.tools import MCP_TOOLS
from app.services.rate_limits import RateLimitedError
from httpx import ASGITransport, AsyncClient
from tests.mcp_server.helpers import (
    MCP_HEADERS,
    fake_session,
    open_mcp_client,
    post_mcp,
    rpc,
)

MCP_URL = "http://test/mcp"


@pytest.fixture
def mcp_env(monkeypatch):
    """Patch settings, auth and DB access around the real HTTP stack."""
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_PUBLIC_URL", MCP_URL)
    monkeypatch.setattr(mcp_auth, "authenticate", AsyncMock(return_value=1))
    monkeypatch.setattr(mcp_server, "async_session", fake_session)
    monkeypatch.setattr(
        mcp_server,
        "_load_user_and_tier",
        AsyncMock(return_value=(SimpleNamespace(id=1), Tier.free)),
    )
    monkeypatch.setattr(mcp_server, "enforce_mcp_limits", AsyncMock())


def _open_client():
    return open_mcp_client("http://test")


async def _post(client, body: dict, token: str | None = "t"):
    return await post_mcp(client, body, token)


@pytest.mark.usefixtures("mcp_env")
async def test_missing_authorization_returns_401_with_metadata():
    async with _open_client() as mcp_client:
        response = await _post(mcp_client, rpc("tools/list"), token=None)
        assert response.status_code == 401
        assert (
            'resource_metadata="http://test/.well-known/oauth-protected-resource/mcp"'
            in response.headers["www-authenticate"]
        )


@pytest.mark.usefixtures("mcp_env")
async def test_invalid_token_returns_401(monkeypatch):
    async with _open_client() as mcp_client:
        monkeypatch.setattr(
            mcp_auth,
            "authenticate",
            AsyncMock(side_effect=mcp_auth._invalid_token()),
        )
        response = await _post(mcp_client, rpc("tools/list"))
        assert response.status_code == 401
        assert response.json() == {"error": "invalid_token"}


@pytest.mark.usefixtures("mcp_env")
async def test_account_not_set_up_returns_403(monkeypatch):
    async with _open_client() as mcp_client:
        monkeypatch.setattr(
            mcp_auth,
            "authenticate",
            AsyncMock(side_effect=mcp_auth.McpAuthError(403, "account_not_set_up")),
        )
        response = await _post(mcp_client, rpc("tools/list"))
        assert response.status_code == 403
        assert response.json()["error"] == "account_not_set_up"


@pytest.mark.usefixtures("mcp_env")
async def test_cookie_without_header_is_ignored():
    async with _open_client() as mcp_client:
        response = await mcp_client.post(
            "/mcp",
            json=rpc("tools/list"),
            headers={**MCP_HEADERS, "Cookie": "__session=x"},
        )
        assert response.status_code == 401


@pytest.mark.usefixtures("mcp_env")
async def test_tools_list():
    async with _open_client() as mcp_client:
        response = await _post(mcp_client, rpc("tools/list"))
        assert response.status_code == 200
        tools = {t["name"]: t for t in response.json()["result"]["tools"]}
        assert set(tools) == set(MCP_TOOLS)
        assert tools["delete_expense"]["annotations"]["destructiveHint"] is True
        assert tools["list_accounts"]["annotations"]["readOnlyHint"] is True


@pytest.mark.usefixtures("mcp_env")
async def test_tools_call_success(monkeypatch):
    async with _open_client() as mcp_client:
        monkeypatch.setattr(
            mcp_server,
            "call_tool",
            AsyncMock(return_value={"success": True, "data": []}),
        )
        response = await _post(
            mcp_client,
            rpc("tools/call", {"name": "list_accounts", "arguments": {}}),
        )
        result = response.json()["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["success"] is True


@pytest.mark.usefixtures("mcp_env")
async def test_tools_call_rate_limited(monkeypatch):
    async with _open_client() as mcp_client:
        monkeypatch.setattr(
            mcp_server,
            "enforce_mcp_limits",
            AsyncMock(side_effect=RateLimitedError(30)),
        )
        response = await _post(
            mcp_client,
            rpc("tools/call", {"name": "list_accounts", "arguments": {}}),
        )
        result = response.json()["result"]
        assert result["isError"] is True
        assert "30" in result["content"][0]["text"]


@pytest.mark.usefixtures("mcp_env")
async def test_post_mcp_is_not_redirected():
    async with _open_client() as mcp_client:
        response = await _post(mcp_client, rpc("tools/list"))
        assert response.status_code != 307


@pytest.mark.usefixtures("mcp_env")
async def test_disabled_returns_404(monkeypatch):
    async with _open_client() as mcp_client:
        monkeypatch.setattr(settings, "MCP_ENABLED", False)
        response = await _post(mcp_client, rpc("tools/list"))
        assert response.status_code == 404
        response = await mcp_client.get("/.well-known/oauth-protected-resource")
        assert response.status_code == 404


@pytest.mark.usefixtures("mcp_env")
async def test_protected_resource_metadata():
    async with _open_client() as mcp_client:
        response = await mcp_client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 200
        body = response.json()
        assert body["resource"] == MCP_URL
        assert body["authorization_servers"] == [settings.CLERK_ISSUER]
        assert response.headers["access-control-allow-origin"] == "*"


@pytest.fixture
def as_metadata(monkeypatch):
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_PUBLIC_URL", MCP_URL)
    monkeypatch.setattr(mcp_http, "_as_cache", None)
    yield
    mcp_http._as_cache = None


@pytest.mark.usefixtures("as_metadata")
async def test_authorization_server_metadata_passthrough(monkeypatch):
    meta = {"issuer": settings.CLERK_ISSUER, "token_endpoint": "https://x/token"}
    monkeypatch.setattr(
        mcp_http, "_fetch_authorization_server_metadata", AsyncMock(return_value=meta)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    assert response.json() == meta
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.usefixtures("as_metadata")
async def test_authorization_server_metadata_upstream_failure(monkeypatch):
    monkeypatch.setattr(
        mcp_http,
        "_fetch_authorization_server_metadata",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 502
