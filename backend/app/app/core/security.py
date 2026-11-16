import base64
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


LOCAL_ALGORITHM = "HS256"
LOCAL_ISSUER = "local"
CLERK_ALGORITHM = "RS256"


def _clerk_public_key() -> str:
    """Decode the base64-encoded Clerk PEM public key on demand.

    Done lazily so importing this module doesn't fail if the key is not
    configured (e.g. during unit tests that don't touch Clerk auth).
    """
    return base64.b64decode(settings.CLERK_JWT_PUBLIC_KEY).decode("utf-8")


def create_access_token(
    user_id: int, expires_delta: timedelta | None = None
) -> str:
    """Issue a local (email/password) access token.

    Minimal claims only: `sub` (user id as string), standard `iat`/`exp`,
    our own `iss` so the verifier can route correctly, and a random `jti`
    so the token has a unique identifier (useful for future revocation).
    No PII is embedded.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    now = datetime.now(timezone.utc)
    to_encode = {
        "sub": str(user_id),
        "iss": LOCAL_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(to_encode, settings.LOCAL_JWT_SECRET, algorithm=LOCAL_ALGORITHM)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


class ClerkKeysUnavailable(RuntimeError):
    """Clerk's JWKS could not be fetched and no keys are cached."""


class ClerkJwks:
    """Cached signing keys from ``{CLERK_ISSUER}/.well-known/jwks.json``.

    Keys are refreshed hourly. An unknown ``kid`` (Clerk may have rotated keys)
    forces one refresh, at most once a minute, so random kids cannot turn into a
    stream of requests to Clerk. While Clerk is unreachable the last good keys
    stay in use.
    """

    TTL_SECONDS = 3600
    FORCE_INTERVAL_SECONDS = 60

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self._keys: list[dict[str, Any]] | None = None
        self._fetched_at = 0.0
        self._forced_at: float | None = None

    async def _fetch(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{settings.CLERK_ISSUER}/.well-known/jwks.json"
            )
            response.raise_for_status()
            return list(response.json().get("keys", []))

    async def _refresh(self) -> None:
        try:
            keys = await self._fetch()
        except Exception as exc:
            if self._keys:
                logger.warning("Clerk JWKS refresh failed, using cached keys: %s", exc)
                return
            raise ClerkKeysUnavailable("Clerk JWKS unavailable") from exc
        self._keys = keys
        self._fetched_at = time.monotonic()

    def _find(self, kid: str | None) -> dict[str, Any] | None:
        return next((k for k in self._keys or [] if k.get("kid") == kid), None)

    async def key_for(self, kid: str | None) -> dict[str, Any] | None:
        """The JWK with this ``kid``, or None if Clerk does not have one."""
        if (
            self._keys is None
            or time.monotonic() - self._fetched_at >= self.TTL_SECONDS
        ):
            await self._refresh()
        key = self._find(kid)
        if key is not None:
            return key
        now = time.monotonic()
        if (
            self._forced_at is None
            or now - self._forced_at >= self.FORCE_INTERVAL_SECONDS
        ):
            self._forced_at = now
            await self._refresh()
            key = self._find(kid)
        return key


clerk_jwks = ClerkJwks()
