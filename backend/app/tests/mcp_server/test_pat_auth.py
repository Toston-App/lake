"""Personal access tokens on /mcp: lookup, user checks and read-only scope."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.config import settings
from app.core.tiers import Tier
from app.mcp_server import auth as mcp_auth
from app.mcp_server import server as mcp_server
from tests.mcp_server.helpers import fake_session, open_mcp_client, post_mcp, rpc

from app import crud

PAT = "tsk_" + "s" * 43


def _user(**overrides) -> SimpleNamespace:
    return SimpleNamespace(
        **{"id": 5, "is_active": True, "is_superuser": False, **overrides}
    )


@pytest.fixture
def pat_lookup(monkeypatch):
    """The DB-backed lookups ``authenticate`` makes for a ``tsk_`` token."""
    state = SimpleNamespace(
        record=SimpleNamespace(id=3, owner_id=5, scope="write"), user=_user()
    )
    monkeypatch.setattr(mcp_auth, "async_session", fake_session)
    state.limiter = AsyncMock()
    monkeypatch.setattr(mcp_auth, "enforce_rate_limit", state.limiter)
    state.over_budget = AsyncMock(return_value=False)
    monkeypatch.setattr(mcp_auth, "rate_limit_reached", state.over_budget)

    async def resolve(_db, token):
        assert token == PAT
        return state.record

    async def get_user(_db, **_kwargs):
        return state.user

    monkeypatch.setattr(mcp_auth.api_tokens, "resolve", resolve)
    monkeypatch.setattr(crud.user, "get", get_user)
    return state


@pytest.mark.usefixtures("pat_lookup")
async def test_write_token_authenticates() -> None:
    assert await mcp_auth.authenticate(PAT) == mcp_auth.AuthResult(5, "write", "pat:3")


async def test_read_token_carries_read_scope(pat_lookup) -> None:
    pat_lookup.record = SimpleNamespace(id=3, owner_id=5, scope="read")
    assert await mcp_auth.authenticate(PAT) == mcp_auth.AuthResult(5, "read", "pat:3")


async def test_unknown_revoked_or_expired_token_is_401(pat_lookup) -> None:
    pat_lookup.record = None
    with pytest.raises(mcp_auth.McpAuthError) as exc_info:
        await mcp_auth.authenticate(PAT)
    assert exc_info.value.status == 401
    assert exc_info.value.error == "invalid_token"


async def test_refused_token_is_cached_and_lookups_are_limited(
    pat_lookup, monkeypatch
) -> None:
    pat_lookup.record = None
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(mcp_auth.api_tokens, "resolve", resolve)

    for _ in range(3):
        with pytest.raises(mcp_auth.McpAuthError):
            await mcp_auth.authenticate(PAT, "203.0.113.9")

    # One database lookup; the retries are answered from the negative cache.
    assert resolve.await_count == 1
    # Only the failure counts toward the IP's budget.
    assert pat_lookup.limiter.await_count == 1
    assert pat_lookup.limiter.await_args.args[0] == "mcp-pat-failures:203.0.113.9"


async def test_ip_over_its_failure_budget_is_429_without_a_lookup(
    pat_lookup, monkeypatch
) -> None:
    pat_lookup.over_budget.return_value = True
    resolve = AsyncMock()
    monkeypatch.setattr(mcp_auth.api_tokens, "resolve", resolve)
    with pytest.raises(mcp_auth.McpAuthError) as exc_info:
        await mcp_auth.authenticate(PAT, "203.0.113.9")
    assert exc_info.value.status == 429
    resolve.assert_not_called()


async def test_valid_token_is_not_counted(pat_lookup) -> None:
    await mcp_auth.authenticate(PAT, "203.0.113.9")
    pat_lookup.limiter.assert_not_called()


@pytest.mark.parametrize(
    ("user", "error"),
    [
        (_user(is_active=False), "inactive_user"),
        (_user(is_superuser=True), "superuser_not_allowed"),
    ],
)
async def test_token_user_checks(pat_lookup, user, error) -> None:
    pat_lookup.user = user
    with pytest.raises(mcp_auth.McpAuthError) as exc_info:
        await mcp_auth.authenticate(PAT)
    assert (exc_info.value.status, exc_info.value.error) == (403, error)


async def test_lookup_failure_is_503_and_never_logs_the_token(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(mcp_auth, "async_session", fake_session)
    monkeypatch.setattr(
        mcp_auth.api_tokens, "resolve", AsyncMock(side_effect=RuntimeError(PAT))
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(mcp_auth.McpAuthError) as exc_info:
        await mcp_auth.authenticate(PAT)

    assert exc_info.value.status == 503
    assert PAT not in caplog.text


@pytest.mark.usefixtures("pat_lookup")
async def test_pat_skips_clerk(monkeypatch) -> None:
    verify = AsyncMock()
    monkeypatch.setattr(mcp_auth, "verify_oauth_identity", verify)
    await mcp_auth.authenticate(PAT)
    verify.assert_not_called()


# -- scope over HTTP ---------------------------------------------------------------


@pytest.fixture
def mcp_env(monkeypatch, pat_lookup):
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_PUBLIC_URL", "http://test/mcp")
    monkeypatch.setattr(mcp_server, "async_session", fake_session)
    monkeypatch.setattr(
        mcp_server,
        "_load_user_and_tier",
        AsyncMock(
            return_value=(SimpleNamespace(id=5, mcp_read_only=False), Tier.tier1)
        ),
    )
    monkeypatch.setattr(mcp_server, "enforce_mcp_limits", AsyncMock())
    call_tool = AsyncMock(return_value={"success": True, "data": []})
    monkeypatch.setattr(mcp_server, "call_tool", call_tool)
    return SimpleNamespace(call_tool=call_tool, lookup=pat_lookup)


# Valid input for each tool: the SDK checks the schema before the handler runs.
ARGUMENTS = {
    "create_expense": {"amount": 5},
    "confirm_action": {"action_id": "a"},
}


async def _call(name: str) -> dict:
    params = {"name": name, "arguments": ARGUMENTS.get(name, {})}
    async with open_mcp_client("http://test") as client:
        response = await post_mcp(client, rpc("tools/call", params), PAT)
    assert response.status_code == 200
    return response.json()["result"]


async def test_write_token_reaches_write_tools(mcp_env) -> None:
    result = await _call("create_expense")
    assert result["isError"] is False
    mcp_env.call_tool.assert_awaited_once()


@pytest.mark.parametrize("name", ["create_expense", "confirm_action", "no_such_tool"])
async def test_read_token_cannot_write(mcp_env, name) -> None:
    mcp_env.lookup.record = SimpleNamespace(id=3, owner_id=5, scope="read")

    result = await _call(name)

    assert result["isError"] is True
    assert "read-only" in result["content"][0]["text"]
    mcp_env.call_tool.assert_not_called()


async def test_calls_are_audited_with_the_client(mcp_env, audit_log) -> None:
    mcp_env.lookup.record = SimpleNamespace(id=3, owner_id=5, scope="read")

    await _call("list_accounts")
    await _call("create_expense")

    first, second = (call.kwargs for call in audit_log.await_args_list)
    assert first["client"] == second["client"] == "pat:3"
    assert (first["tool_name"], first["status"]) == ("list_accounts", "completed")
    assert (second["status"], second["error"]) == ("refused", "read_only_token")


async def test_failed_and_gated_calls_are_audited(mcp_env, audit_log) -> None:
    mcp_env.call_tool.return_value = {
        "success": True,
        "status": "confirmation_required",
        "action_id": "act-1",
    }
    await _call("create_expense")
    mcp_env.call_tool.return_value = {"success": False, "error": "Expense not found"}
    await _call("confirm_action")

    staged, failed = (call.kwargs for call in audit_log.await_args_list)
    assert (staged["status"], staged["action_id"]) == ("confirmation_required", "act-1")
    assert failed["status"] == "failed"
    assert failed["action_id"] == "a"
    # Tool errors can quote the user's data, so none is stored.
    assert failed["error"] is None


async def test_read_token_can_read(mcp_env) -> None:
    mcp_env.lookup.record = SimpleNamespace(id=3, owner_id=5, scope="read")

    result = await _call("list_accounts")

    assert result["isError"] is False
    mcp_env.call_tool.assert_awaited_once()


async def test_read_only_account_cannot_write(mcp_env, monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "_load_user_and_tier",
        AsyncMock(return_value=(SimpleNamespace(id=5, mcp_read_only=True), Tier.tier1)),
    )

    refused = await _call("create_expense")
    allowed = await _call("list_accounts")

    assert refused["isError"] is True
    assert "only read" in refused["content"][0]["text"]
    assert allowed["isError"] is False
    mcp_env.call_tool.assert_awaited_once()
