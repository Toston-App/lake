import base64
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

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
    user_id: int, expires_delta: Optional[timedelta] = None
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
