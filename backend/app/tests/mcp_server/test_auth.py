"""Unit tests for MCP token verification and the Clerk helpers it uses."""

import time

import httpx
import pytest
from app.core import security
from app.core.config import settings
from app.mcp_server import auth as mcp_auth
from app.services import clerk as clerk_service
from tests.mcp_server.helpers import make_key, sign_token

MCP_URL = "http://test/mcp"


@pytest.fixture
def signing(monkeypatch, jwks_fetch) -> bytes:
    monkeypatch.setattr(settings, "MCP_PUBLIC_URL", MCP_URL)
    pem, public_jwk = make_key()
    jwks_fetch.return_value = [public_jwk]
    return pem


async def _verify(token: str) -> str | None:
    """The token's subject, or None when it is refused with 401."""
    try:
        return await mcp_auth.verify_oauth_token(token)
    except mcp_auth.McpAuthError as exc:
        assert exc.status == 401
        return None


# -- JWT access tokens -----------------------------------------------------------


async def test_valid_token(signing):
    assert await _verify(sign_token(signing, aud=MCP_URL)) == "user_abc"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "http://test/other"},
        {"aud": None},
        {"aud": MCP_URL, "exp": int(time.time()) - 60},
        {"aud": MCP_URL, "iss": "https://evil.example"},
        {"aud": MCP_URL, "sid": "sess_1"},
        {"aud": MCP_URL, "typ": "JWT"},
    ],
    ids=["wrong-aud", "no-aud", "expired", "wrong-iss", "session", "not-at+jwt"],
)
async def test_rejected_jwt(signing, overrides):
    assert await _verify(sign_token(signing, **overrides)) is None


async def test_trailing_slash_audience_matches(signing):
    assert await _verify(sign_token(signing, aud=MCP_URL + "/")) == "user_abc"


@pytest.mark.usefixtures("signing")
async def test_other_key_same_kid():
    other_pem, _ = make_key()
    assert await _verify(sign_token(other_pem, aud=MCP_URL)) is None


@pytest.mark.usefixtures("signing")
async def test_garbage_token():
    assert await _verify("not-a-jwt") is None


# -- Clerk JWKS cache ------------------------------------------------------------


async def test_unknown_kid_refetch_is_throttled(signing, jwks_fetch):
    assert await _verify(sign_token(signing, aud=MCP_URL)) == "user_abc"
    assert jwks_fetch.await_count == 1
    for kid in ("unknown-1", "unknown-2"):
        assert await _verify(sign_token(signing, aud=MCP_URL, kid=kid)) is None
    # Initial load + exactly one forced refetch; the second unknown kid is throttled.
    assert jwks_fetch.await_count == 2


async def test_jwks_outage_keeps_cached_keys(signing, jwks_fetch, monkeypatch):
    assert await _verify(sign_token(signing, aud=MCP_URL)) == "user_abc"
    monkeypatch.setattr(security.clerk_jwks, "_fetched_at", 0.0)  # expire the cache
    jwks_fetch.side_effect = httpx.ConnectError("down")
    assert await _verify(sign_token(signing, aud=MCP_URL)) == "user_abc"


async def test_jwks_outage_without_keys_is_503(signing, jwks_fetch):
    jwks_fetch.side_effect = httpx.ConnectError("down")
    with pytest.raises(mcp_auth.McpAuthError) as exc_info:
        await mcp_auth.verify_oauth_token(sign_token(signing, aud=MCP_URL))
    assert exc_info.value.status == 503
    assert exc_info.value.headers["Retry-After"]


# -- Clerk opaque-token lookup -----------------------------------------------------


@pytest.fixture
def clerk_api(monkeypatch):
    """Route services.clerk's HTTP calls to a handler the test sets."""
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", "sk_test_fake")
    state = {"handler": lambda request: httpx.Response(200, json={})}
    real_client = httpx.AsyncClient

    def client(**kwargs):
        transport = httpx.MockTransport(lambda request: state["handler"](request))
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(clerk_service.httpx, "AsyncClient", client)
    return state


async def test_clerk_verify_returns_record(clerk_api):
    record = {"object": "clerk_idp_oauth_access_token", "subject": "user_abc"}

    def handler(request):
        assert request.headers["authorization"] == "Bearer sk_test_fake"
        return httpx.Response(200, json=record)

    clerk_api["handler"] = handler
    assert await clerk_service.verify_oauth_access_token("oat_x") == record


async def test_clerk_verify_unknown_token_is_none(clerk_api):
    clerk_api["handler"] = lambda request: httpx.Response(404, json={})
    assert await clerk_service.verify_oauth_access_token("oat_x") is None


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_clerk_verify_outage_raises(clerk_api, status):
    clerk_api["handler"] = lambda request: httpx.Response(status)
    with pytest.raises(clerk_service.ClerkUnavailable):
        await clerk_service.verify_oauth_access_token("oat_x")


async def test_clerk_verify_network_error_raises(clerk_api):
    def handler(_request):
        raise httpx.ConnectError("down")

    clerk_api["handler"] = handler
    with pytest.raises(clerk_service.ClerkUnavailable):
        await clerk_service.verify_oauth_access_token("oat_x")


# -- middleware ------------------------------------------------------------------


async def test_middleware_does_not_mutate_shared_state(monkeypatch):
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_PUBLIC_URL", MCP_URL)

    async def authenticate(_token, _client_ip):
        return 7

    monkeypatch.setattr(mcp_auth, "authenticate", authenticate)
    seen = {}

    async def inner(scope, _receive, _send):
        seen.update(scope["state"])

    shared = {"other": 1}
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer t")],
        "state": shared,
    }
    await mcp_auth.BearerAuthMiddleware(inner)(scope, None, None)
    assert seen == {"other": 1, "toston_user_id": 7}
    assert shared == {"other": 1}
