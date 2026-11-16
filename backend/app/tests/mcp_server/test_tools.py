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


def _today() -> str:
    """Computed per test, so a run that crosses midnight UTC doesn't flake."""
    return mcp_tools._today(None).isoformat()


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
    from app.ai.dispatch import HANDLERS
    from app.ai.tool_specs import TOOL_SPECS

    chat_only = {"search_web", "submit_feedback"}
    expected = set(TOOL_SPECS) | {mcp_tools.CONFIRM_TOOL, mcp_tools.CANCEL_TOOL}

    assert set(mcp_tools.MCP_TOOLS) == expected
    # Every shared tool has a handler; only the chat-only ones aren't shared.
    assert set(HANDLERS) - set(TOOL_SPECS) == chat_only
    assert set(TOOL_ARG_MODELS) - set(TOOL_SPECS) == chat_only | {
        "ask_user",
        "render_data",
    }


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


def test_time_zone_is_offered_only_where_a_date_is_defaulted() -> None:
    for tool in mcp_tools.MCP_TOOLS.values():
        properties = mcp_tools.input_schema(tool).get("properties", {})
        assert (mcp_tools.TIME_ZONE_FIELD in properties) == (
            tool.name in mcp_tools.TIME_ZONE_TOOLS
        ), tool.name


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
    assert mocks.execute.call_args.args[1]["date"] == _today()

    await mcp_tools.call_tool(
        "create_income", {"amount": 5, "date": "2026-01-02"}, DB, USER
    )
    assert mocks.execute.call_args.args[1]["date"] == "2026-01-02"


async def test_staged_creates_get_a_date_before_preview(mocks) -> None:
    await mcp_tools.call_tool(
        "create_transfer", {"amount": 5, "from_acc": 1, "to_acc": 2}, DB, USER
    )
    assert mocks.preview.call_args.args[3]["date"] == _today()

    result = await mcp_tools.call_tool(
        "bulk_create_transactions",
        {"expenses": [{"amount": 1}, {"amount": 2, "date": "2026-01-02"}]},
        DB,
        USER,
    )
    stored = await _assert_gated(result, mocks)
    assert [i["date"] for i in stored["tool_args"]["expenses"]] == [
        _today(),
        "2026-01-02",
    ]


async def test_time_zone_sets_today(mocks) -> None:
    # Kiritimati is UTC+14, so its date is ahead of UTC's for most of the day.
    zone = "Pacific/Kiritimati"
    today = mcp_tools._today(zone).isoformat()

    await mcp_tools.call_tool(
        "create_expense", {"amount": 5, "time_zone": zone}, DB, USER
    )

    args = mocks.execute.call_args.args[1]
    assert args["date"] == today
    assert "time_zone" not in args


async def test_unknown_time_zone_is_refused(mocks) -> None:
    result = await mcp_tools.call_tool(
        "create_expense", {"amount": 5, "time_zone": "Mars/Olympus"}, DB, USER
    )

    assert result["success"] is False
    mocks.execute.assert_not_called()


async def test_time_zone_defaults_balance_adjustment_and_recurring(mocks) -> None:
    zone = "America/Bogota"
    result = await mcp_tools.call_tool(
        "create_balance_adjustment",
        {"account_id": 1, "new_balance": 10, "time_zone": zone},
        DB,
        USER,
    )
    stored = await _assert_gated(result, mocks)
    assert stored["tool_args"]["adjustment_date"] == mcp_tools._today(zone).isoformat()

    result = await mcp_tools.call_tool(
        "create_recurring",
        {
            "type": "expense",
            "amount": 9.99,
            "frequency": "monthly",
            "start_date": "2026-01-01",
            "time_zone": zone,
        },
        DB,
        USER,
    )
    stored = await _assert_gated(result, mocks)
    assert stored["tool_args"]["timezone"] == zone


@pytest.mark.parametrize(
    "name",
    [
        "list_recurring",
        "compare_periods",
        "list_places",
        "get_portfolio_summary",
        "list_holdings",
        "list_investment_transactions",
    ],
)
async def test_reads_run_immediately(mocks, monkeypatch, name) -> None:
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", True)
    args = (
        {"date_filter_type": "month", "date": "2026-10"}
        if name == "compare_periods"
        else {}
    )

    result = await mcp_tools.call_tool(name, args, DB, USER)

    assert result["success"] is True
    mocks.execute.assert_awaited_once()
    assert mocks.redis.data == {}


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("delete_expense", {"id": 7}),
        ("bulk_delete_transactions", {"expenses": [1]}),
        ("manage_account", {"action": "delete", "id": 3}),
        (
            "create_recurring",
            {
                "type": "expense",
                "amount": 9.99,
                "frequency": "monthly",
                "start_date": "2026-01-01",
            },
        ),
        (
            "create_recurring_from_transaction",
            {
                "transaction_type": "expense",
                "transaction_id": 4,
                "frequency": "monthly",
            },
        ),
        ("update_recurring", {"id": 2, "data": {"is_active": False}}),
        ("skip_recurring_occurrence", {"id": 2}),
        ("delete_recurring", {"id": 2, "delete_generated": True}),
        ("update_profile", {"currency": "EUR"}),
        ("detect_recurring", {"use_ai": True}),
        ("export_data", {}),
        (
            "convert_transaction",
            {"from_type": "expense", "id": 7, "to_type": "transfer", "to_acc": 2},
        ),
    ],
)
async def test_risky_calls_are_gated(mocks, monkeypatch, name, args) -> None:
    # Tools behind a feature are only offered when it is on.
    monkeypatch.setattr("app.services.r2.is_configured", lambda: True)
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
        ("delete_recurring", {"id": 2}, {"delete_generated": True}),
        (
            "convert_transaction",
            {"from_type": "expense", "id": 7, "to_type": "transfer", "to_acc": 2},
            {"to_acc": 9},
        ),
        (
            "manage_category",
            {"action": "update", "id": 4, "data": {"name": "x"}},
            {"action": "delete"},
        ),
    ],
    ids=[
        "destructive",
        "bulk-delete-targets",
        "delete-recurring-scope",
        "convert-target",
        "manage-update-to-delete",
    ],
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
        await mcp_limits.enforce_mcp_limits(DB, USER, Tier.free)

    assert exc_info.value.status_code == 402
    # A rejected free user must not use up rate-limit quota.
    assert limiter.await_count == 0

    await mcp_limits.enforce_mcp_limits(DB, USER, Tier.tier1)
    assert limiter.await_count == 2


async def test_monthly_cap_applies_when_the_plan_sets_one(monkeypatch) -> None:
    import dataclasses

    limiter = AsyncMock()
    monkeypatch.setattr(mcp_limits, "enforce_rate_limit", limiter)
    used = AsyncMock(return_value=10)
    monkeypatch.setattr(mcp_limits, "calls_this_month", used)
    capped = dataclasses.replace(mcp_limits.TIERS[Tier.free], mcp_monthly_call_limit=10)
    monkeypatch.setitem(mcp_limits.TIERS, Tier.free, capped)

    with pytest.raises(mcp_limits.McpMonthlyLimitReached):
        await mcp_limits.enforce_mcp_limits(DB, USER, Tier.free)
    assert limiter.await_count == 0

    # Unlimited plans never count.
    await mcp_limits.enforce_mcp_limits(DB, USER, Tier.tier1)
    assert used.await_count == 1


# -- review fixes ----------------------------------------------------------------


async def test_confirm_edit_of_flat_args_keeps_them_flat(mocks) -> None:
    action_id = await _seed("update_profile", {"name": "Alex", "default_account_id": 5})

    result = await mcp_tools.call_tool(
        "confirm_action",
        {"action_id": action_id, "edits": {"currency": "EUR"}},
        DB,
        USER,
    )

    stored = await _assert_gated(result, mocks)
    args = stored["tool_args"]
    assert (args["name"], args["default_account_id"], args["currency"]) == (
        "Alex",
        5,
        "EUR",
    )
    assert "data" not in args and "id" not in args


async def test_confirm_refuses_when_the_record_changed(mocks) -> None:
    await redis_utils.store_pending_action(
        action_id="a",
        user_id=USER.id,
        conversation_id="mcp",
        tool_name="delete_recurring",
        tool_args={"id": 2},
        preview={"record_version": "old"},
    )
    mocks.preview.return_value = {"record_version": "new"}

    result = await mcp_tools.call_tool("confirm_action", {"action_id": "a"}, DB, USER)

    assert result["success"] is False
    assert "changed" in result["error"]
    mocks.execute.assert_not_called()


@pytest.mark.usefixtures("mocks")
@pytest.mark.parametrize("enabled", [True, False])
async def test_investment_tools_follow_the_feature_flag(monkeypatch, enabled) -> None:
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", enabled)
    names = {tool.name for tool in mcp_tools.enabled_tools()}

    result = await mcp_tools.call_tool("list_holdings", {}, DB, USER)

    assert ("list_holdings" in names) is enabled
    assert result["success"] is enabled


@pytest.mark.parametrize(
    "executed_at",
    ["2026-11-01T10:00:00", "yesterday"],
    ids=["no-time-zone", "not-a-date"],
)
async def test_bad_investment_date_is_refused_when_staging(
    mocks, monkeypatch, executed_at
) -> None:
    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", True)

    result = await mcp_tools.call_tool(
        "record_investment_transaction",
        {
            "holding_id": 1,
            "transaction_type": "buy",
            "quantity": 1,
            "price_per_unit": 1,
            "executed_at": executed_at,
        },
        DB,
        USER,
    )

    assert result["success"] is False
    assert "executed_at" in result["error"]
    assert mocks.redis.data == {}


# -- language --------------------------------------------------------------------


async def test_language_sets_the_approval_wording(mocks) -> None:
    result = await mcp_tools.call_tool(
        "delete_expense", {"id": 7, "language": "es"}, DB, USER
    )

    stored = await _assert_gated(result, mocks)
    assert stored["response_language"] == "es"
    assert stored["tool_args"]["response_language"] == "es"
    assert result["message"].startswith("Revisa")
    assert "Spanish" in result["next_step"]


@pytest.mark.parametrize("country", ["es-MX", "MXN", "ARS", "COP"])
async def test_language_defaults_to_the_users_locale(mocks, country) -> None:
    """Older accounts store the currency code; those markets speak Spanish."""
    spanish_user = SimpleNamespace(id=USER.id, country=country)

    result = await mcp_tools.call_tool("delete_expense", {"id": 7}, DB, spanish_user)

    stored = await _assert_gated(result, mocks)
    assert stored["response_language"] == "es"


@pytest.mark.parametrize("country", ["en-US", "USD", "de-DE", None])
async def test_other_locales_get_english(mocks, country) -> None:
    user = SimpleNamespace(id=USER.id, country=country)

    result = await mcp_tools.call_tool("delete_expense", {"id": 7}, DB, user)

    stored = await _assert_gated(result, mocks)
    assert stored["response_language"] == "en"


@pytest.mark.usefixtures("mocks")
async def test_confirm_errors_follow_the_language() -> None:
    result = await mcp_tools.call_tool(
        "confirm_action", {"action_id": "missing", "language": "es"}, DB, USER
    )

    assert result["success"] is False
    assert result["error"].startswith("La acción no existe")


async def test_unknown_language_is_refused(mocks) -> None:
    result = await mcp_tools.call_tool(
        "delete_expense", {"id": 7, "language": "fr"}, DB, USER
    )

    assert result["success"] is False
    assert mocks.redis.data == {}


def test_language_is_offered_where_toston_writes_the_wording() -> None:
    def offered(name: str) -> bool:
        schema = mcp_tools.input_schema(mcp_tools.MCP_TOOLS[name])
        return mcp_tools.LANGUAGE_FIELD in schema.get("properties", {})

    assert offered("delete_expense")
    assert offered("confirm_action")
    assert offered("cancel_action")
    assert not offered("list_accounts")
