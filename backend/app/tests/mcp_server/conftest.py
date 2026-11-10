"""Shared fixtures for the MCP server tests: no integration database needed."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from app.core import security
from app.mcp_server import auth as mcp_auth
from app.utilities import redis as redis_utils
from tests.mcp_server.helpers import FakeRedis


@pytest.fixture(scope="session", autouse=True)
def setup_database() -> None:
    """Overrides the suite-wide fixture: these tests never touch Postgres."""


@pytest.fixture(autouse=True)
def _fresh_auth_caches():
    security.clerk_jwks.clear()
    mcp_auth.rejected_opaque_tokens.clear()
    yield
    security.clerk_jwks.clear()
    mcp_auth.rejected_opaque_tokens.clear()


@pytest.fixture
def jwks_fetch(monkeypatch) -> AsyncMock:
    """Clerk's JWKS endpoint; set ``return_value`` to the list of keys."""
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(security.clerk_jwks, "_fetch", fetch)
    return fetch


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(redis_utils, "r", fake)
    return fake
