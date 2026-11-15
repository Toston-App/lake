"""API tests for /api/v1/api-tokens and the token service."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from app.core.config import settings
from app.models import ApiToken, McpToolCall, User
from app.services import api_tokens as api_token_service
from app.services import mcp_audit
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import create_test_user

PREFIX = "/api/v1/api-tokens"


@pytest.fixture(autouse=True)
def _mcp_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_PUBLIC_URL", "https://api.example.com/mcp")


async def _create(client: AsyncClient, **body) -> dict:
    response = await client.post(PREFIX, json={"name": "CI", **body})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_returns_plaintext_once_and_stores_only_its_hash(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    created = await _create(client, scope="read", expires_in_days=30)

    token = created["token"]
    assert token.startswith("tsk_")
    assert created["prefix"] == token[:12]
    assert created["scope"] == "read"
    assert created["expires_at"] is not None
    row = await db_session.get(ApiToken, created["id"])
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.token_hash != token


async def test_no_tokens_while_ai_connections_are_off(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "MCP_ENABLED", False)

    created = await client.post(PREFIX, json={"name": "CI"})
    connections = (await client.get(f"{PREFIX}/connections")).json()

    assert created.status_code == 403
    assert (connections["enabled"], connections["mcp_url"]) == (False, None)


async def test_list_never_exposes_the_token(client: AsyncClient) -> None:
    await _create(client)

    response = await client.get(PREFIX)

    assert response.status_code == 200
    [listed] = response.json()
    assert "token" not in listed
    assert "token_hash" not in listed


async def test_revoke_removes_token_from_list(client: AsyncClient) -> None:
    created = await _create(client)

    assert (await client.delete(f"{PREFIX}/{created['id']}")).status_code == 204
    assert (await client.get(PREFIX)).json() == []
    assert (await client.delete(f"{PREFIX}/{created['id']}")).status_code == 404


async def test_cannot_revoke_another_users_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    other = await create_test_user(db_session, email="other@example.com")
    token, _ = await api_token_service.create_token(db_session, other, "x", "write")

    response = await client.delete(f"{PREFIX}/{token.id}")

    assert response.status_code == 404
    await db_session.refresh(token)
    assert token.revoked_at is None


async def test_active_token_cap(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "API_TOKENS_MAX_PER_USER", 2)
    await _create(client)
    second = await _create(client)

    assert (await client.post(PREFIX, json={"name": "third"})).status_code == 409

    # A revoked token no longer counts.
    await client.delete(f"{PREFIX}/{second['id']}")
    await _create(client)


async def test_rejects_bad_input(client: AsyncClient) -> None:
    for body in (
        {"name": ""},
        {"name": "x", "scope": "admin"},
        {"name": "x", "expires_in_days": 0},
        {"name": "x", "expires_in_days": 366},
    ):
        assert (await client.post(PREFIX, json=body)).status_code == 422, body


async def test_token_is_not_accepted_by_the_rest_api(
    unauth_client: AsyncClient, db_session: AsyncSession, test_user: User
) -> None:
    _, plaintext = await api_token_service.create_token(
        db_session, test_user, "x", "write"
    )
    headers = {"Authorization": f"Bearer {plaintext}"}

    assert (
        await unauth_client.get("/api/v1/accounts", headers=headers)
    ).status_code == 401
    # A token can't mint more tokens either.
    response = await unauth_client.post(PREFIX, json={"name": "y"}, headers=headers)
    assert response.status_code == 401


# -- resolve ------------------------------------------------------------------------


async def test_resolve_live_token_records_use(
    db_session: AsyncSession, test_user: User
) -> None:
    token, plaintext = await api_token_service.create_token(
        db_session, test_user, "x", "read"
    )

    resolved = await api_token_service.resolve(db_session, plaintext)

    assert resolved is not None and resolved.id == token.id
    await db_session.refresh(token)
    assert token.last_used_at is not None


@pytest.mark.parametrize("state", ["revoked", "expired", "unknown"])
async def test_resolve_refuses_dead_tokens(
    db_session: AsyncSession, test_user: User, state: str
) -> None:
    token, plaintext = await api_token_service.create_token(
        db_session, test_user, "x", "write"
    )
    if state == "revoked":
        await api_token_service.revoke_token(db_session, test_user, token.id)
    elif state == "expired":
        token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db_session.commit()
    else:
        plaintext = plaintext + "x"

    assert await api_token_service.resolve(db_session, plaintext) is None


async def test_tokens_are_deleted_with_their_user(
    db_session: AsyncSession,
) -> None:
    user = await create_test_user(db_session, email="gone@example.com")
    await api_token_service.create_token(db_session, user, "x", "write")

    await db_session.delete(user)
    await db_session.commit()

    rows = await db_session.execute(
        select(ApiToken).where(ApiToken.owner_id == user.id)
    )
    assert rows.scalars().all() == []


# -- AI connections and audit retention ------------------------------------------


def _call(user_id: int, client: str | None, days_ago: float = 0) -> McpToolCall:
    return McpToolCall(
        user_id=user_id,
        client=client,
        tool_name="list_accounts",
        status="completed",
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


async def test_connections_group_calls_by_client(
    client: AsyncClient, db_session: AsyncSession, test_user: User
) -> None:
    token, _ = await api_token_service.create_token(db_session, test_user, "CI", "read")
    other = await create_test_user(db_session, email="else@example.com")
    db_session.add_all(
        [
            _call(test_user.id, "oauth:claude", days_ago=40),
            _call(test_user.id, "oauth:claude", days_ago=1),
            _call(test_user.id, f"pat:{token.id}"),
            _call(test_user.id, "oauth:old", days_ago=200),
            _call(other.id, "oauth:theirs"),
        ]
    )
    await db_session.commit()

    response = await client.get(f"{PREFIX}/connections")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["mcp_url"] == "https://api.example.com/mcp"
    pat, oauth = body["connections"]
    assert (pat["kind"], pat["token_name"], pat["token_active"]) == (
        "token",
        "CI",
        True,
    )
    assert (oauth["client"], oauth["calls_last_30_days"]) == ("oauth:claude", 1)


async def test_audit_rows_past_retention_are_pruned(
    db_session: AsyncSession, test_user: User
) -> None:
    db_session.add_all(
        [
            _call(test_user.id, "oauth:x", days_ago=1),
            _call(test_user.id, "oauth:x", 120),
        ]
    )
    await db_session.commit()

    await mcp_audit.prune(db_session, test_user.id)
    await db_session.commit()

    rows = await db_session.execute(
        select(McpToolCall).where(McpToolCall.user_id == test_user.id)
    )
    assert len(rows.scalars().all()) == 1
