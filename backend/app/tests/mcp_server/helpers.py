"""Helpers shared by the MCP server tests (fixtures live in conftest.py)."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.core.config import settings
from app.main import app
from app.mcp_server.http import new_session_manager
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwk, jwt

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


# -- signing keys and tokens ---------------------------------------------------


def make_key(kid: str = "k1") -> tuple[bytes, dict]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_jwk = jwk.construct(public_pem, "RS256").to_dict()
    public_jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return pem, public_jwk


def sign_token(
    pem: bytes, *, aud: str | None, kid: str = "k1", typ: str = "at+jwt", **claims
) -> str:
    """An OAuth access token as Clerk would sign it; ``None`` drops a claim."""
    payload = {
        "iss": settings.CLERK_ISSUER,
        "aud": aud,
        "sub": "user_abc",
        "exp": int(time.time()) + 600,
        **claims,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid, "typ": typ})


@asynccontextmanager
async def fake_session():
    """Stands in for ``async_session()`` where the DB is mocked out."""
    yield SimpleNamespace()


# -- HTTP client -----------------------------------------------------------------


@asynccontextmanager
async def open_mcp_client(base_url: str):
    """A client for the real app with a running MCP session manager.

    The manager holds an anyio task group, which must be entered and exited in
    one task, so tests open it themselves rather than through a fixture.
    """
    async with new_session_manager().run():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=base_url
        ) as client:
            yield client


async def post_mcp(client: AsyncClient, body: dict, token: str | None):
    headers = dict(MCP_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post("/mcp", json=body, headers=headers)


def rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


# -- Redis -----------------------------------------------------------------------


class FakeRedis:
    """The few Upstash calls the pending-action store makes, in memory.

    Every call yields to the event loop first, so concurrent callers interleave
    the way they would against a real server.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        await asyncio.sleep(0)
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        await asyncio.sleep(0)
        self.data[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        await asyncio.sleep(0)
        return sum(self.data.pop(key, None) is not None for key in keys)
