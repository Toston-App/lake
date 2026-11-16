"""The REST API accepts Clerk session tokens but never OAuth access tokens (/mcp only)."""

import time

import pytest
from app.api import deps
from app.core import security
from app.core.config import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

ISSUER = "https://clerk.example.com"


@pytest.fixture
def private_pem(monkeypatch) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setattr(security, "_clerk_public_key", lambda: public_pem)
    monkeypatch.setattr(settings, "CLERK_ISSUER", ISSUER)
    monkeypatch.setattr(
        settings, "CLERK_AUTHORIZED_PARTIES", ["https://app.example.com"]
    )
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _token(pem: bytes, typ: str) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "iat": now,
        "nbf": now,
        "sub": "user_abc",
        "azp": "https://app.example.com",
        "exp": now + 600,
        "sid": "sess_1",
    }
    return jwt.encode(claims, pem, algorithm="RS256", headers={"typ": typ})


def test_session_token_is_accepted(private_pem) -> None:
    assert deps.verify_clerk_token(_token(private_pem, "JWT")).sub == "user_abc"


@pytest.mark.parametrize("typ", ["at+jwt", "application/at+jwt", "AT+JWT"])
def test_oauth_access_token_is_refused(private_pem, typ) -> None:
    with pytest.raises(jwt.JWTError):
        deps.verify_clerk_token(_token(private_pem, typ))
