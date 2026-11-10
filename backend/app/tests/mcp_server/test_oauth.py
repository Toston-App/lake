"""End-to-end OAuth tests for /mcp: discovery, Clerk sign-in, then tool calls.

Unlike test_http.py, nothing between the HTTP request and the tool handler is
mocked here: the real BearerAuthMiddleware, token verification (JWT and opaque
introspection) and user resolution run. Only the network edges are faked:
Clerk's JWKS and Backend API verify endpoint, the user table and the tools.
"""

import asyncio
import time
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.core import security
from app.core.config import settings
from app.core.tiers import Tier
from app.mcp_server import auth as mcp_auth
from app.mcp_server import http as mcp_http
from app.mcp_server import server as mcp_server
from app.services.clerk import ClerkUnavailable
from tests.mcp_server.helpers import (
    fake_session,
    make_key,
    open_mcp_client,
    post_mcp,
    rpc,
    sign_token,
)

MCP_URL = "https://mcp.example.test/mcp"
OTHER_RESOURCE = "https://other.example.test/mcp"
# Clerk user id -> local user id
USERS = {"user_alice": 1, "user_bob": 2, "user_carol": 3, "user_admin": 99}
ADMIN_ID = 99


class FakeClerk:
    """The parts of a Clerk instance the resource server talks to.

    `sign_in` stands in for the browser OAuth flow (authorize + consent +
    token exchange) and returns the access token Clerk would hand the client.
    """

    def __init__(self) -> None:
        self.opaque: dict[str, dict] = {}
        self.pem, self.jwk = make_key()
        self.verify_calls = 0
        self._ids = count(1)

    def sign_in(self, user: str, *, fmt: str = "opaque", resource: str = MCP_URL):
        if fmt == "jwt":
            return sign_token(self.pem, sub=user, aud=resource)
        token = f"oat_{next(self._ids):032x}"
        now = int(time.time())
        self.opaque[token] = {
            "object": "clerk_idp_oauth_access_token",
            "id": token,
            "client_id": "client_test",
            "subject": user,
            "scopes": ["openid", "profile", "email"],
            "aud": [resource],
            "revoked": False,
            "revocation_reason": None,
            "expired": False,
            "expiration": now + 86_400,
            "created_at": now,
            "updated_at": now,
        }
        return token

    async def verify(self, token: str) -> dict | None:
        """Stands in for services.clerk.verify_oauth_access_token."""
        self.verify_calls += 1
        await asyncio.sleep(0)  # yield, so concurrent requests interleave
        record = self.opaque.get(token)
        return dict(record) if record is not None else None


@pytest.fixture
def clerk(monkeypatch) -> FakeClerk:
    fake = FakeClerk()
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_PUBLIC_URL", MCP_URL)
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(mcp_auth, "verify_oauth_access_token", fake.verify)
    monkeypatch.setattr(
        security.clerk_jwks, "_fetch", AsyncMock(return_value=[fake.jwk])
    )

    async def get_by_uuid(_db, *, uuid):
        return SimpleNamespace(id=USERS[uuid]) if uuid in USERS else None

    monkeypatch.setattr(mcp_auth, "async_session", fake_session)
    monkeypatch.setattr(mcp_auth.crud.user, "get_by_uuid", get_by_uuid)
    monkeypatch.setattr(mcp_auth.crud.user, "is_active", lambda _user: True)
    monkeypatch.setattr(
        mcp_auth.crud.user, "is_superuser", lambda user: user.id == ADMIN_ID
    )
    monkeypatch.setattr(mcp_auth, "enforce_rate_limit", AsyncMock(return_value=True))

    async def load_user(_db, user_id):
        return SimpleNamespace(id=user_id), Tier.free

    async def call_tool(name, _arguments, _db, user):
        await asyncio.sleep(0)
        return {"success": True, "data": {"tool": name, "user_id": user.id}}

    monkeypatch.setattr(mcp_server, "async_session", fake_session)
    monkeypatch.setattr(mcp_server, "_load_user_and_tier", load_user)
    monkeypatch.setattr(mcp_server, "enforce_mcp_limits", AsyncMock())
    monkeypatch.setattr(mcp_server, "call_tool", call_tool)
    return fake


def _client():
    return open_mcp_client("https://mcp.example.test")


def _call(request_id: int = 1, tool: str = "list_accounts") -> dict:
    return rpc("tools/call", {"name": tool, "arguments": {}}, request_id)


_post = post_mcp


def _assert_tool_ok(response, user_id: int, request_id: int = 1) -> None:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == request_id
    result = body["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["data"]["user_id"] == user_id


# -- discovery -----------------------------------------------------------------


def _challenge_param(header: str, name: str) -> str | None:
    marker = f'{name}="'
    if marker not in header:
        return None
    return header.split(marker, 1)[1].split('"', 1)[0]


@pytest.mark.usefixtures("clerk")
async def test_discovery_from_challenge_to_authorization_server(monkeypatch):
    clerk_meta = {"issuer": settings.CLERK_ISSUER, "token_endpoint": "https://x/t"}
    fetch = AsyncMock(return_value=clerk_meta)
    monkeypatch.setattr(mcp_http, "_fetch_authorization_server_metadata", fetch)
    monkeypatch.setattr(mcp_http, "_as_cache", None)

    async with _client() as client:
        challenge = (await _post(client, _call(), None)).headers["www-authenticate"]
        metadata_url = _challenge_param(challenge, "resource_metadata")
        assert metadata_url == (
            "https://mcp.example.test/.well-known/oauth-protected-resource/mcp"
        )

        prm = await client.get(metadata_url)
        assert prm.status_code == 200
        body = prm.json()
        assert body["resource"] == MCP_URL
        assert body["authorization_servers"] == [settings.CLERK_ISSUER]
        assert body["bearer_methods_supported"] == ["header"]

        # Compatibility proxy of the issuer's RFC 8414 document.
        asm = await client.get("/.well-known/oauth-authorization-server")
        assert asm.json()["issuer"] == body["authorization_servers"][0]


@pytest.mark.usefixtures("clerk")
async def test_metadata_path_is_derived_from_resource_uri(monkeypatch):
    resource = "https://api.example.test/v2/mcp"
    monkeypatch.setattr(settings, "MCP_PUBLIC_URL", resource)
    assert mcp_auth.protected_resource_metadata_url(resource) == (
        "https://api.example.test/.well-known/oauth-protected-resource/v2/mcp"
    )
    async with _client() as client:
        hit = await client.get("/.well-known/oauth-protected-resource/v2/mcp")
        assert hit.status_code == 200
        assert hit.json()["resource"] == resource
        # Only the exact resource is described.
        miss = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert miss.status_code == 404


async def test_authorization_server_metadata_comes_from_clerk_issuer(monkeypatch):
    seen = {}

    async def fake_get(_self, url, **_kwargs):
        seen["url"] = url
        return httpx.Response(200, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    await mcp_http._fetch_authorization_server_metadata()
    assert seen["url"] == (
        f"{settings.CLERK_ISSUER}/.well-known/oauth-authorization-server"
    )


# -- sign-in then tool call ----------------------------------------------------


@pytest.mark.parametrize("fmt", ["opaque", "jwt"])
async def test_sign_in_then_authenticated_tool_call(clerk, fmt):
    async with _client() as client:
        first = await _post(client, _call(), None)
        assert first.status_code == 401

        token = clerk.sign_in("user_alice", fmt=fmt)
        _assert_tool_ok(await _post(client, _call(), token), user_id=1)


# -- rejections ----------------------------------------------------------------


def _expired_opaque(clerk: FakeClerk) -> str:
    token = clerk.sign_in("user_alice")
    clerk.opaque[token].update(expired=True)
    return token


def _past_expiration_opaque(clerk: FakeClerk) -> str:
    # Clerk has not flipped `expired` yet, but the timestamp has passed.
    token = clerk.sign_in("user_alice")
    clerk.opaque[token].update(expiration=int(time.time()) - 1)
    return token


def _revoked_opaque(clerk: FakeClerk) -> str:
    token = clerk.sign_in("user_alice")
    clerk.opaque[token].update(revoked=True, revocation_reason="user")
    return token


def _no_audience_opaque(clerk: FakeClerk) -> str:
    token = clerk.sign_in("user_alice")
    del clerk.opaque[token]["aud"]
    return token


def _prefix_audience_opaque(clerk: FakeClerk) -> str:
    return clerk.sign_in("user_alice", resource=MCP_URL + "/extra")


REJECTED = {
    "opaque_expired_flag": _expired_opaque,
    "opaque_expiration_passed": _past_expiration_opaque,
    "opaque_revoked": _revoked_opaque,
    "opaque_wrong_audience": lambda c: c.sign_in("user_alice", resource=OTHER_RESOURCE),
    "opaque_audience_not_exact": _prefix_audience_opaque,
    "opaque_no_audience": _no_audience_opaque,
    "opaque_unknown_to_clerk": lambda c: "oat_" + "0" * 32,
    "jwt_expired": lambda c: sign_token(
        c.pem, sub="user_alice", aud=MCP_URL, exp=int(time.time()) - 60
    ),
    "jwt_wrong_audience": lambda c: c.sign_in(
        "user_alice", fmt="jwt", resource=OTHER_RESOURCE
    ),
    "jwt_wrong_issuer": lambda c: sign_token(
        c.pem, sub="user_alice", aud=MCP_URL, iss="https://evil.example"
    ),
    "jwt_forged_signature": lambda c: sign_token(
        make_key()[0], sub="user_alice", aud=MCP_URL
    ),
    # A Clerk session token: typ JWT, sid claim, no resource audience.
    "session_token": lambda c: sign_token(
        c.pem, typ="JWT", sub="user_alice", aud=None, sid="sess_1", azp="https://app"
    ),
    # Even a session-shaped token that somehow carried our audience.
    "session_token_with_audience": lambda c: sign_token(
        c.pem, typ="JWT", sub="user_alice", aud=MCP_URL, sid="sess_1"
    ),
    "at_jwt_with_session_id": lambda c: sign_token(
        c.pem, sub="user_alice", aud=MCP_URL, sid="sess_1"
    ),
    "garbage": lambda c: "not-a-token",
}


@pytest.mark.parametrize("case", list(REJECTED))
async def test_invalid_tokens_get_invalid_token_challenge(clerk, case):
    token = REJECTED[case](clerk)
    async with _client() as client:
        response = await _post(client, _call(), token)
    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}
    header = response.headers["www-authenticate"]
    assert header.startswith("Bearer ")
    assert _challenge_param(header, "error") == "invalid_token"
    assert _challenge_param(header, "resource_metadata") == (
        mcp_auth.protected_resource_metadata_url(MCP_URL)
    )


@pytest.mark.usefixtures("clerk")
async def test_missing_token_challenge_has_no_error_code():
    async with _client() as client:
        response = await _post(client, _call(), None)
    assert response.status_code == 401
    header = response.headers["www-authenticate"]
    assert _challenge_param(header, "resource_metadata")
    assert _challenge_param(header, "error") is None
    assert _challenge_param(header, "scope") == "openid profile email"


async def test_opaque_token_rejected_without_secret_key(clerk, monkeypatch):
    token = clerk.sign_in("user_alice")
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", None)
    async with _client() as client:
        response = await _post(client, _call(), token)
    assert response.status_code == 401
    assert clerk.verify_calls == 0


async def test_clerk_unavailable_is_503_not_invalid_token(clerk, monkeypatch):
    # A 401 would make the client discard a valid token and redo OAuth. Which
    # Clerk responses count as "unavailable" is tested in test_auth.py.
    token = clerk.sign_in("user_alice")
    monkeypatch.setattr(
        mcp_auth,
        "verify_oauth_access_token",
        AsyncMock(side_effect=ClerkUnavailable("down")),
    )
    async with _client() as client:
        response = await _post(client, _call(), token)
    assert response.status_code == 503
    assert response.headers["retry-after"]
    assert "www-authenticate" not in response.headers


async def test_rejected_opaque_token_is_not_reintrospected(clerk):
    async with _client() as client:
        for _ in range(5):
            response = await _post(client, _call(), "oat_" + "0" * 32)
            assert response.status_code == 401
    assert clerk.verify_calls == 1


async def test_opaque_introspection_is_rate_limited_per_ip(clerk, monkeypatch):
    from app.services.rate_limits import RateLimitedError

    monkeypatch.setattr(
        mcp_auth, "enforce_rate_limit", AsyncMock(side_effect=RateLimitedError(17))
    )
    token = clerk.sign_in("user_alice")
    async with _client() as client:
        response = await _post(client, _call(), token)
    assert response.status_code == 429
    assert clerk.verify_calls == 0


@pytest.mark.parametrize("fmt", ["opaque", "jwt"])
async def test_trailing_slash_audience_is_accepted(clerk, fmt):
    token = clerk.sign_in("user_alice", fmt=fmt, resource=MCP_URL + "/")
    async with _client() as client:
        _assert_tool_ok(await _post(client, _call(), token), user_id=1)


async def test_opaque_expiration_in_milliseconds_is_checked(clerk):
    token = clerk.sign_in("user_alice")
    clerk.opaque[token]["expiration"] = int((time.time() - 60) * 1000)
    async with _client() as client:
        assert (await _post(client, _call(), token)).status_code == 401


@pytest.mark.parametrize("fmt", ["opaque", "jwt"])
async def test_superuser_cannot_connect(clerk, fmt):
    token = clerk.sign_in("user_admin", fmt=fmt)
    async with _client() as client:
        response = await _post(client, _call(), token)
    assert response.status_code == 403
    assert response.json()["error"] == "superuser_not_allowed"


async def test_revocation_takes_effect_on_next_call(clerk):
    token = clerk.sign_in("user_alice")
    async with _client() as client:
        _assert_tool_ok(await _post(client, _call(), token), user_id=1)
        clerk.opaque[token]["revoked"] = True
        assert (await _post(client, _call(), token)).status_code == 401


# -- stateless transport: repeated and concurrent calls -----------------------


@pytest.mark.parametrize("fmt", ["opaque", "jwt"])
async def test_repeated_calls_each_authenticated(clerk, fmt):
    token = clerk.sign_in("user_alice", fmt=fmt)
    async with _client() as client:
        for i in range(1, 26):
            _assert_tool_ok(
                await _post(client, _call(i), token), user_id=1, request_id=i
            )
    if fmt == "opaque":
        assert clerk.verify_calls == 25  # no request rides on an earlier one


async def test_concurrent_calls_keep_users_apart(clerk):
    tokens = {
        "user_alice": clerk.sign_in("user_alice"),
        "user_bob": clerk.sign_in("user_bob", fmt="jwt"),
        "user_carol": clerk.sign_in("user_carol"),
    }
    bad = clerk.sign_in("user_alice", resource=OTHER_RESOURCE)
    plan = []
    for i in range(1, 61):
        user = list(tokens)[i % 3]
        plan.append((i, user, tokens[user]))

    async with _client() as client:
        good = asyncio.gather(*(_post(client, _call(i), t) for i, _, t in plan))
        rejected = asyncio.gather(
            *(_post(client, _call(100 + i), bad) for i in range(10))
        )
        good_responses, bad_responses = await asyncio.gather(good, rejected)

    for (i, user, _), response in zip(plan, good_responses, strict=True):
        _assert_tool_ok(response, user_id=USERS[user], request_id=i)
    assert all(r.status_code == 401 for r in bad_responses)


async def test_concurrent_initialize_and_list_without_session(clerk):
    """Stateless: no Mcp-Session-Id is issued, and requests need none."""
    token = clerk.sign_in("user_alice")
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    async with _client() as client:
        responses = await asyncio.gather(
            *(_post(client, init if i % 2 else listing, token) for i in range(20))
        )
    for response in responses:
        assert response.status_code == 200
        assert "mcp-session-id" not in response.headers
        assert "result" in response.json()
