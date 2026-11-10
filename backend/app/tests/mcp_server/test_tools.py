"""Unit tests for the transport-agnostic MCP tool core.

Pending actions go through the real ``ai.confirmation`` lifecycle and the real
Redis serialization, against an in-memory Redis. Only the dispatcher
(``execute_tool``) and the DB-backed preview builder are mocked.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.ai import confirmation
from app.ai.confirmation import requires_confirmation
from app.ai.tools import TOOL_ARG_MODELS
from app.core.config import settings
from app.core.tiers import Tier
from app.mcp_server import limits as mcp_limits
from app.mcp_server import tools as mcp_tools
from app.schemas.expense import ExpenseCreate
from app.schemas.income import IncomeCreate
from app.utilities import redis as redis_utils

USER = SimpleNamespace(id=1)
DB = SimpleNamespace()
TODAY = mcp_tools.datetime.now(mcp_tools.timezone.utc).date().isoformat()


@pytest.fixture
def mocks(monkeypatch, fake_redis):
    execute = AsyncMock(return_value=({"success": True, "data": {}}, "x"))
    preview = AsyncMock(return_value={"operation": "preview"})
    monkeypatch.setattr(mcp_tools, "execute_tool", execute)
    monkeypatch.setattr(confirmation, "build_pending_preview", preview)
    return SimpleNamespace(execute=execute, preview=preview, redis=fake_redis)


async def _seed(tool_name: str, tool_args: dict, conversation_id: str = "mcp") -> str:
    await redis_utils.store_pending_action(
        action_id="a",
        user_id=USER.id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        tool_args=tool_args,
        preview={},
    )
    return "a"


async def _pending(action_id: str) -> dict | None:
    return await redis_utils.get_pending_action(action_id, USER.id)


async def _assert_gated(result, mocks) -> dict:
    """The call was staged, not run; returns what was stored."""
    mocks.execute.assert_not_called()
    assert result["status"] == "confirmation_required"
    stored = await _pending(result["action_id"])
    assert stored is not None
    assert stored["conversation_id"] == "mcp"
    return stored


# -- registry --------------------------------------------------------------------


def test_registry_covers_dispatcher_tools_and_hides_chat_only_ones() -> None:
    chat_only = {"ask_user", "render_data", "search_web", "submit_feedback"}
    no_arg_reads = {
        "get_user_profile",
        "list_accounts",
        "list_categories",
        "list_places",
    }
    expected = (
        (set(TOOL_ARG_MODELS) - chat_only)
        | no_arg_reads
        | {
            mcp_tools.CONFIRM_TOOL,
            mcp_tools.CANCEL_TOOL,
        }
    )

    assert set(mcp_tools.MCP_TOOLS) == expected


def test_input_schemas_are_self_contained_and_hide_chat_fields() -> None:
    for tool in mcp_tools.MCP_TOOLS.values():
        blob = str(mcp_tools.input_schema(tool))
        for banned in ("$ref", "$defs", "response_language", "presentation"):
            assert banned not in blob, (tool.name, banned)


def test_flags_and_descriptions_follow_the_confirmation_policy() -> None:
    for tool in mcp_tools.MCP_TOOLS.values():
        if tool.read_only:
            assert requires_confirmation(tool.name, {}) is False
            assert tool.destructive is False
            continue
        if tool.handler is not None:
            continue
        if tool.name.startswith("manage_"):
            assert requires_confirmation(tool.name, {"action": "delete"})
            assert not requires_confirmation(tool.name, {"action": "create"})
            assert "updates and deletes require confirmation" in tool.description
        elif requires_confirmation(tool.name, {}):
            assert tool.description.endswith("Requires confirmation.")
        else:
            assert tool.description.endswith("It runs immediately.")
        if tool.name.startswith(("delete_", "bulk_delete", "update_", "manage_")):
            assert tool.destructive, tool.name


@pytest.mark.parametrize("schema", [ExpenseCreate, IncomeCreate])
def test_mcp_is_an_accepted_made_from(schema) -> None:
    record = schema(amount=1, made_from=mcp_tools.MADE_FROM_MCP)
    assert record.made_from == "MCP"


# -- direct and staged calls -----------------------------------------------------


async def test_create_expense_executes_with_mcp_source(mocks) -> None:
    result = await mcp_tools.call_tool("create_expense", {"amount": 5}, DB, USER)

    assert result["success"] is True
    mocks.execute.assert_awaited_once()
    assert mocks.execute.call_args.kwargs["made_from"] == "MCP"
    assert mocks.redis.data == {}


async def test_create_without_date_defaults_to_today(mocks) -> None:
    await mcp_tools.call_tool("create_expense", {"amount": 5}, DB, USER)
    assert mocks.execute.call_args.args[1]["date"] == TODAY

    await mcp_tools.call_tool(
        "create_income", {"amount": 5, "date": "2026-01-02"}, DB, USER
    )
    assert mocks.execute.call_args.args[1]["date"] == "2026-01-02"


async def test_staged_creates_get_a_date_before_preview(mocks) -> None:
    await mcp_tools.call_tool(
        "create_transfer", {"amount": 5, "from_acc": 1, "to_acc": 2}, DB, USER
    )
    assert mocks.preview.call_args.args[3]["date"] == TODAY

    result = await mcp_tools.call_tool(
        "bulk_create_transactions",
        {"expenses": [{"amount": 1}, {"amount": 2, "date": "2026-01-02"}]},
        DB,
        USER,
    )
    stored = await _assert_gated(result, mocks)
    assert [i["date"] for i in stored["tool_args"]["expenses"]] == [
        TODAY,
        "2026-01-02",
    ]


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("delete_expense", {"id": 7}),
        ("bulk_delete_transactions", {"expenses": [1]}),
        ("manage_account", {"action": "delete", "id": 3}),
    ],
)
async def test_risky_calls_are_gated(mocks, name, args) -> None:
    result = await mcp_tools.call_tool(name, args, DB, USER)

    stored = await _assert_gated(result, mocks)
    assert stored["tool_name"] == name


async def test_manage_account_create_runs(mocks) -> None:
    created = await mcp_tools.call_tool(
        "manage_account", {"action": "create", "data": {"name": "Cash"}}, DB, USER
    )

    assert created["success"] is True
    mocks.execute.assert_awaited_once()
    assert mocks.redis.data == {}


async def test_invalid_args_store_nothing(mocks) -> None:
    result = await mcp_tools.call_tool("delete_expense", {"id": "abc"}, DB, USER)

    assert result["success"] is False
    assert mocks.redis.data == {}
    mocks.execute.assert_not_called()


@pytest.mark.parametrize("count", [0, settings.MCP_BULK_MAX_ITEMS + 1])
async def test_bulk_size_is_checked(mocks, count) -> None:
    items = [{"amount": 1}] * count

    result = await mcp_tools.call_tool(
        "bulk_create_transactions", {"expenses": items}, DB, USER
    )

    assert result["success"] is False
    assert mocks.redis.data == {}
    mocks.execute.assert_not_called()


# -- confirm and cancel ------------------------------------------------------------


async def test_stage_then_confirm_round_trip(mocks) -> None:
    staged = await mcp_tools.call_tool("delete_expense", {"id": 7}, DB, USER)

    result = await mcp_tools.call_tool(
        "confirm_action", {"action_id": staged["action_id"]}, DB, USER
    )

    assert result["success"] is True
    args = mocks.execute.call_args
    assert args.args[:2] == ("delete_expense", {"id": 7, "response_language": "en"})
    assert args.kwargs["confirmed"] is True
    assert args.kwargs["made_from"] == "MCP"
    assert await _pending(staged["action_id"]) is None


async def test_confirm_runs_at_most_once(mocks) -> None:
    action_id = await _seed("delete_expense", {"id": 7})

    results = await asyncio.gather(
        *(
            mcp_tools.call_tool("confirm_action", {"action_id": action_id}, DB, USER)
            for _ in range(5)
        )
    )

    assert sum(r["success"] for r in results) == 1
    mocks.execute.assert_awaited_once()


async def test_confirm_unknown_action_is_refused(mocks) -> None:
    result = await mcp_tools.call_tool("confirm_action", {"action_id": "a"}, DB, USER)

    assert result["success"] is False
    mocks.execute.assert_not_called()


async def test_confirm_refuses_and_keeps_in_app_chat_action(mocks) -> None:
    action_id = await _seed("delete_expense", {"id": 7}, conversation_id="chat-1")

    result = await mcp_tools.call_tool(
        "confirm_action", {"action_id": action_id}, DB, USER
    )

    assert result["success"] is False
    mocks.execute.assert_not_called()
    assert await _pending(action_id) is not None


async def test_confirm_with_edits_returns_new_preview(mocks) -> None:
    action_id = await _seed(
        "create_transfer",
        {"amount": 5, "from_acc": 1, "to_acc": 2, "date": "2026-01-02"},
    )

    result = await mcp_tools.call_tool(
        "confirm_action",
        {"action_id": action_id, "edits": {"amount": 9}},
        DB,
        USER,
    )

    # The user approved amount 5, not 9: nothing runs until they approve again.
    stored = await _assert_gated(result, mocks)
    assert stored["tool_name"] == "create_transfer"
    assert stored["tool_args"]["amount"] == 9
    assert stored["tool_args"]["from_acc"] == 1
    assert result["action_id"] != action_id
    assert await _pending(action_id) is None


async def test_confirm_edit_cannot_retarget_record_id(mocks) -> None:
    action_id = await _seed(
        "update_expense", {"id": 7, "data": {"amount": 1}, "response_language": "en"}
    )

    result = await mcp_tools.call_tool(
        "confirm_action",
        {"action_id": action_id, "edits": {"id": 999, "amount": 3}},
        DB,
        USER,
    )

    stored = await _assert_gated(result, mocks)
    assert stored["tool_args"]["id"] == 7
    assert stored["tool_args"]["data"]["amount"] == 3


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "edits"),
    [
        ("delete_expense", {"id": 7}, {"amount": 1}),
        ("bulk_delete_transactions", {"expenses": [1]}, {"expenses": [99]}),
        (
            "manage_category",
            {"action": "update", "id": 4, "data": {"name": "x"}},
            {"action": "delete"},
        ),
    ],
    ids=["destructive", "bulk-delete-targets", "manage-update-to-delete"],
)
async def test_confirm_refuses_unsafe_edits(mocks, tool_name, tool_args, edits) -> None:
    action_id = await _seed(tool_name, tool_args)

    result = await mcp_tools.call_tool(
        "confirm_action", {"action_id": action_id, "edits": edits}, DB, USER
    )

    assert result["success"] is False
    mocks.execute.assert_not_called()


@pytest.mark.usefixtures("mocks")
async def test_cancel_discards_mcp_action() -> None:
    action_id = await _seed("delete_expense", {"id": 7})

    result = await mcp_tools.call_tool(
        "cancel_action", {"action_id": action_id}, DB, USER
    )

    assert result["success"] is True
    assert await _pending(action_id) is None


@pytest.mark.usefixtures("mocks")
async def test_cancel_refuses_in_app_chat_action() -> None:
    action_id = await _seed("delete_expense", {"id": 7}, conversation_id="chat-1")

    result = await mcp_tools.call_tool(
        "cancel_action", {"action_id": action_id}, DB, USER
    )

    assert result["success"] is False
    assert await _pending(action_id) is not None


# -- limits ----------------------------------------------------------------------


async def test_enforce_mcp_limits_requires_paid_plan(monkeypatch) -> None:
    limiter = AsyncMock()
    monkeypatch.setattr(mcp_limits, "enforce_rate_limit", limiter)
    monkeypatch.setattr(settings, "MCP_REQUIRES_PAID", True)

    with pytest.raises(mcp_limits.McpPaidTierRequired) as exc_info:
        await mcp_limits.enforce_mcp_limits(USER, Tier.free)

    assert exc_info.value.status_code == 402
    # A rejected free user must not use up rate-limit quota.
    assert limiter.await_count == 0

    await mcp_limits.enforce_mcp_limits(USER, Tier.tier1)
    assert limiter.await_count == 2
