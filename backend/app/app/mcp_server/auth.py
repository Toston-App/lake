"""Bearer-token authentication for the /mcp endpoint.

Clerk is the OAuth authorization server; Toston is only the resource server.
Clerk issues OAuth access tokens in one of two formats, chosen in the Clerk
dashboard, and both are accepted:

* JWT (header ``typ: at+jwt``, RFC 9068): verified locally against Clerk's JWKS
  (``core.security.clerk_jwks``) for signature, issuer, expiry and audience.
* Opaque (``oat_`` prefix): introspected with Clerk's Backend API
  (``services.clerk.verify_oauth_access_token``), which is the only way to
  authenticate them. The record's ``aud``, ``revoked``, ``expired`` and
  ``expiration`` are checked here because Clerk's helper does not check the
  audience of opaque tokens.

Either way the audience must contain MCP_PUBLIC_URL (a trailing slash on either
side is ignored). Clerk session tokens are rejected: they are not ``at+jwt``,
carry a ``sid`` and have no resource audience. Only the Authorization header is
read (never cookies), so a browser cannot be tricked into making authenticated
MCP calls (CSRF).

Personal access tokens (``tsk_`` prefix, ``services.api_tokens``) are the
fallback for clients that can only send a static header. They are looked up
by hash in Toston's database and carry a scope: a ``read`` token may only call
read-only tools, which ``server.py`` enforces. OAuth tokens get ``write``.

When Clerk itself cannot answer (timeout, 429, 5xx, JWKS down with no cached
keys) the request gets 503, not 401: a 401 ``invalid_token`` makes clients
discard a perfectly good token and send the user through OAuth again.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from jose import jwt
from starlette.responses import JSONResponse

from app import crud
from app.core.config import settings
from app.core.security import ClerkKeysUnavailable, clerk_jwks
from app.db.session import async_session
from app.schemas.api_token import ApiTokenScope as TokenScope
from app.services import api_tokens
from app.services.clerk import ClerkUnavailable, verify_oauth_access_token
from app.services.rate_limits import (
    RateLimitedError,
    enforce_rate_limit,
    rate_limit_reached,
)

logger = logging.getLogger(__name__)

_OPAQUE_PREFIX = "oat_"
# RFC 9068 section 2.1; the same values Clerk's own SDK uses to tell OAuth JWTs
# apart from session JWTs.
_OAUTH_JWT_TYPES = frozenset({"at+jwt", "application/at+jwt"})
_RETRY_AFTER_SECONDS = 30


class McpAuthError(Exception):
    """Why a request to /mcp is refused, and the HTTP response that says so."""

    def __init__(
        self,
        status: int,
        error: str,
        message: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(error)
        self.status = status
        self.error = error
        self.message = message
        self.headers = headers or {}

    def response(self) -> JSONResponse:
        body: dict[str, Any] = {"error": self.error}
        if self.message:
            body["message"] = self.message
        return JSONResponse(body, status_code=self.status, headers=self.headers)


def _challenge(extra: str = "") -> dict[str, str]:
    metadata_url = protected_resource_metadata_url(settings.MCP_PUBLIC_URL or "")
    value = f'Bearer resource_metadata="{metadata_url}", scope="openid profile email"'
    return {"WWW-Authenticate": value + extra}


def _missing_token() -> McpAuthError:
    # RFC 6750 section 3.1: no error code when no credentials were sent.
    return McpAuthError(401, "invalid_token", headers=_challenge())


def _invalid_token() -> McpAuthError:
    return McpAuthError(
        401,
        "invalid_token",
        headers=_challenge(
            ', error="invalid_token", error_description="The access token is '
            'invalid, expired, or not issued for this resource"'
        ),
    )


def _retry_later(status: int, error: str) -> McpAuthError:
    return McpAuthError(
        status, error, headers={"Retry-After": str(_RETRY_AFTER_SECONDS)}
    )


def protected_resource_metadata_url(resource: str) -> str:
    """RFC 9728 section 3.1: insert the well-known segment between the origin
    and the resource path, e.g. https://h/mcp -> https://h/.well-known/oauth-protected-resource/mcp.
    """
    parts = urlsplit(resource)
    path = parts.path.rstrip("/")
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            f"/.well-known/oauth-protected-resource{path}",
            "",
            "",
        )
    )


def _audience_matches(audiences: Any, resource: str) -> bool:
    if isinstance(audiences, str):
        audiences = [audiences]
    if not isinstance(audiences, list):
        return False
    return any(
        isinstance(aud, str) and aud.rstrip("/") == resource.rstrip("/")
        for aud in audiences
    )


@dataclass(frozen=True)
class OAuthIdentity:
    """The Clerk user an OAuth token belongs to, and the client it was issued to."""

    sub: str
    client_id: str | None = None


def _client_of(claims: dict[str, Any]) -> str | None:
    # RFC 9068 names it client_id; azp is the OIDC spelling.
    client = claims.get("client_id") or claims.get("azp")
    return client[:200] if isinstance(client, str) and client else None


async def _verify_jwt(token: str, resource: str) -> OAuthIdentity | None:
    header = jwt.get_unverified_header(token)
    key = await clerk_jwks.key_for(header.get("kid"))
    if key is None:
        return None
    # The header is covered by the signature, so after a successful decode its
    # typ is authentic; reading it first only selects the key.
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        issuer=settings.CLERK_ISSUER,
        options={
            "require_exp": True,
            "require_iss": True,
            # The audience is required and checked below, ignoring a trailing
            # slash on either side; jose's require_aud would force its own
            # exact-match check back on.
            "verify_aud": False,
        },
    )
    if not _audience_matches(claims.get("aud"), resource):
        logger.info("MCP token rejected: audience does not include this resource")
        return None
    if str(header.get("typ", "")).lower() not in _OAUTH_JWT_TYPES:
        logger.info("MCP token rejected: JWT is not an OAuth access token")
        return None
    if "sid" in claims:
        logger.info("MCP token rejected: session token")
        return None
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    return OAuthIdentity(sub, _client_of(claims))


class _RejectedTokens:
    """Opaque OAuth tokens and personal access tokens that were refused, by
    sha256, so retrying one bad token does not call Clerk or the database
    again. Valid tokens are never cached, so revocation stays immediate."""

    TTL_SECONDS = 60
    MAX_ENTRIES = 10_000

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def __contains__(self, digest: str) -> bool:
        rejected_at = self._seen.get(digest)
        if rejected_at is None:
            return False
        if time.monotonic() - rejected_at < self.TTL_SECONDS:
            return True
        del self._seen[digest]
        return False

    def add(self, digest: str) -> None:
        if len(self._seen) >= self.MAX_ENTRIES:
            cutoff = time.monotonic() - self.TTL_SECONDS
            self._seen = {k: t for k, t in self._seen.items() if t >= cutoff}
            if len(self._seen) >= self.MAX_ENTRIES:
                self._seen.clear()
        self._seen[digest] = time.monotonic()

    def clear(self) -> None:
        self._seen.clear()


rejected_tokens = _RejectedTokens()


def _identity_of_valid_record(
    data: dict[str, Any], resource: str
) -> OAuthIdentity | None:
    if data.get("object") != "clerk_idp_oauth_access_token":
        return None  # includes {"active": false}
    if data.get("revoked") is not False or data.get("expired") is not False:
        return None
    expiration = data.get("expiration")
    if not isinstance(expiration, (int, float)):
        return None
    if expiration > 10_000_000_000:  # milliseconds, not seconds
        expiration = expiration / 1000
    if expiration <= time.time():
        return None
    if not _audience_matches(data.get("aud"), resource):
        logger.info("MCP token rejected: audience does not include this resource")
        return None
    subject = data.get("subject")
    if not isinstance(subject, str) or not subject:
        return None
    return OAuthIdentity(subject, _client_of(data))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _limit_lookups(kind: str, client_ip: str | None) -> None:
    """Cap token lookups made before any user is known, per client IP.

    Each costs a Clerk API call or a database query whatever the token is.
    """
    if not client_ip:
        return
    try:
        await enforce_rate_limit(
            f"{kind}:{client_ip}",
            settings.MCP_INTROSPECT_PER_IP_PER_MINUTE,
            window_seconds=60,
            fail_open=True,
        )
    except RateLimitedError:
        raise _retry_later(429, "rate_limited") from None


async def _introspect_opaque(
    token: str, resource: str, client_ip: str | None
) -> OAuthIdentity | None:
    """Resource-aware introspection of an opaque Clerk OAuth access token.

    The secret key scopes the lookup to this Clerk instance, the one that
    CLERK_ISSUER names, so a token from another instance is unknown there.
    Each lookup spends the instance's Backend API quota before any user is
    known, so lookups are capped per client IP and rejections are cached.
    """
    if not settings.CLERK_SECRET_KEY:
        logger.warning("MCP opaque token rejected: CLERK_SECRET_KEY is not set")
        return None
    digest = _digest(token)
    if digest in rejected_tokens:
        return None
    await _limit_lookups("mcp-introspect", client_ip)
    record = await verify_oauth_access_token(token)
    identity = _identity_of_valid_record(record, resource) if record else None
    if identity is None:
        rejected_tokens.add(digest)
    return identity


async def verify_oauth_identity(
    token: str, client_ip: str | None = None
) -> OAuthIdentity:
    """The Clerk user and OAuth client of a valid access token for this resource.

    Raises McpAuthError: 401 for a bad token, 429 when this client made too
    many lookups, 503 when Clerk cannot answer.
    """
    resource = settings.MCP_PUBLIC_URL
    if not resource:
        raise _invalid_token()
    try:
        if token.startswith(_OPAQUE_PREFIX):
            identity = await _introspect_opaque(token, resource, client_ip)
        elif token.count(".") == 2:
            identity = await _verify_jwt(token, resource)
        else:
            identity = None
    except McpAuthError:
        raise
    except (ClerkUnavailable, ClerkKeysUnavailable) as exc:
        logger.warning("MCP auth unavailable: %s", exc)
        raise _retry_later(503, "temporarily_unavailable") from None
    except Exception as exc:  # noqa: BLE001 - any other failure means "not authenticated"
        logger.info("MCP token rejected: %s", type(exc).__name__)
        identity = None
    if identity is None:
        raise _invalid_token()
    return identity


@dataclass(frozen=True)
class AuthResult:
    """Who is calling, and what the credential allows."""

    user_id: int
    scope: TokenScope = "write"
    # Who is calling, for the audit log: "oauth:<client id>" or "pat:<token id>".
    client: str | None = None


async def _authenticate_pat(token: str, client_ip: str | None) -> AuthResult:
    """Look a personal access token up by hash.

    Only failed lookups count against the per-IP cap: valid tokens are used
    on every request, so counting them would throttle real clients sharing an
    address. An IP over its failure budget is refused before any query.
    """
    digest = _digest(token)
    if digest in rejected_tokens:
        raise _invalid_token()
    failures_key = f"mcp-pat-failures:{client_ip}"
    if client_ip and await rate_limit_reached(
        failures_key, settings.MCP_INTROSPECT_PER_IP_PER_MINUTE, window_seconds=60
    ):
        raise _retry_later(429, "rate_limited")
    try:
        async with async_session() as db:
            record = await api_tokens.resolve(db, token)
            user = await crud.user.get(db, id=record.owner_id) if record else None
    except Exception as exc:  # noqa: BLE001 - never echo the token
        logger.warning("MCP token lookup failed: %s", type(exc).__name__)
        raise _retry_later(503, "temporarily_unavailable") from None
    if record is None or user is None:
        rejected_tokens.add(digest)
        await _limit_lookups("mcp-pat-failures", client_ip)
        raise _invalid_token()
    _check_user(user)
    scope: TokenScope = "read" if record.scope == "read" else "write"
    return AuthResult(user.id, scope, f"pat:{record.id}")


async def authenticate(token: str, client_ip: str | None = None) -> AuthResult:
    """Map a bearer token to a local user and scope, or raise McpAuthError.

    The single place that decides who may use MCP.
    """
    if token.startswith(api_tokens.TOKEN_PREFIX):
        return await _authenticate_pat(token, client_ip)
    identity = await verify_oauth_identity(token, client_ip)
    async with async_session() as db:
        user = await crud.user.get_by_uuid(db, uuid=identity.sub)
    if user is None:
        raise McpAuthError(
            403,
            "account_not_set_up",
            "Sign in to Toston once in the app to finish creating your account, "
            "then reconnect.",
        )
    _check_user(user)
    client = f"oauth:{identity.client_id}" if identity.client_id else "oauth"
    return AuthResult(user.id, client=client)


def _check_user(user) -> None:
    if not crud.user.is_active(user):
        raise McpAuthError(403, "inactive_user")
    if crud.user.is_superuser(user):
        # Superusers can act on every user's records; an external LLM host with
        # untrusted context must never hold that power.
        raise McpAuthError(
            403,
            "superuser_not_allowed",
            "Admin accounts can't connect over MCP. Use a regular account.",
        )


def _bearer_token(scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            token = token.strip()
            if scheme.lower() == "bearer" and token:
                return token
            return None
    return None


class BearerAuthMiddleware:
    """Pure ASGI middleware that authenticates every request to the MCP app."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not settings.MCP_ENABLED or not settings.MCP_PUBLIC_URL:
            await JSONResponse({"detail": "Not Found"}, status_code=404)(
                scope, receive, send
            )
            return
        try:
            token = _bearer_token(scope)
            if token is None:
                raise _missing_token()
            client = scope.get("client")
            auth = await authenticate(token, client[0] if client else None)
        except McpAuthError as exc:
            await exc.response()(scope, receive, send)
            return
        scope["state"] = {
            **(scope.get("state") or {}),
            "toston_user_id": auth.user_id,
            "toston_token_scope": auth.scope,
            "toston_client": auth.client,
        }
        await self.app(scope, receive, send)
