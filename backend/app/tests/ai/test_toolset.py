import pytest
from app.ai.agent import build_toolset
from app.core.config import settings


def test_every_tier_gets_the_full_chat_toolset(monkeypatch):
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "key")

    tools = set(build_toolset().tools)

    assert {
        "get_period_summary",
        "get_period_charts",
        "bulk_create_transactions",
        "bulk_delete_transactions",
        "search_web",
        "delete_expense",
        "render_data",
    } <= tools


def test_web_search_still_needs_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "TAVILY_API_KEY", None)

    assert "search_web" not in build_toolset().tools


@pytest.mark.parametrize("investments", [True, False])
def test_chat_and_mcp_offer_the_same_shared_tools(monkeypatch, investments):
    from app.ai.tool_specs import enabled_specs
    from app.mcp_server.tools import CANCEL_TOOL, CONFIRM_TOOL, enabled_tools

    monkeypatch.setattr(settings, "INVESTMENTS_ENABLED", investments)
    chat = set(build_toolset().tools)
    mcp = {tool.name for tool in enabled_tools()} - {CONFIRM_TOOL, CANCEL_TOOL}

    assert mcp == {spec.name for spec in enabled_specs()}
    assert mcp <= chat
    assert ("list_holdings" in chat) is investments
    # The schema comes from the shared argument model.
    schema = build_toolset().tools["update_recurring"].tool_def.parameters_json_schema
    assert {"id", "data"} <= set(schema["properties"])


async def test_generated_tool_stages_gated_calls_and_runs_reads(monkeypatch):
    from datetime import date
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.ai import agent_tools
    from app.ai.tools import TOOL_ARG_MODELS

    defer = AsyncMock(return_value="deferred")
    execute = AsyncMock(
        return_value=({"success": True, "data": [1], "has_more": False}, "x")
    )
    monkeypatch.setattr(agent_tools, "execute_tool", execute)
    tools = {t.name: t.function for t in agent_tools.agent_tools(defer)}
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            db=None, user=None, time_zone="Europe/Madrid", local_date=date(2026, 1, 2)
        )
    )

    staged = await tools["create_recurring"](
        ctx,
        TOOL_ARG_MODELS["create_recurring"](
            type="expense",
            amount=9,
            frequency="monthly",
            start_date="2026-01-01",
            response_language="es",
        ),
    )
    read = await tools["list_recurring"](ctx, TOOL_ARG_MODELS["list_recurring"]())

    assert staged == "deferred"
    name, args, language = defer.await_args.args[1:]
    assert (name, args["timezone"], language) == (
        "create_recurring",
        "Europe/Madrid",
        "es",
    )
    assert read.return_value == {"data": [1], "has_more": False}
    assert read.metadata == []


def test_every_gated_tool_can_name_its_approval_language():
    """A staged call needs response_language for its approval card."""
    from app.ai.confirmation import requires_confirmation
    from app.ai.tools import TOOL_ARG_MODELS

    gated = [
        name
        for name in TOOL_ARG_MODELS
        if requires_confirmation(name, {"action": "update"})
    ]
    assert "detect_recurring" in gated
    missing = [
        name
        for name in gated
        if "response_language" not in TOOL_ARG_MODELS[name].model_fields
    ]
    assert not missing


async def test_presentation_is_offered_only_where_a_ui_part_exists():
    from types import SimpleNamespace

    from app.ai.agent_tools import hide_presentation

    tools = build_toolset().tools
    compare = await hide_presentation(
        SimpleNamespace(), tools["compare_periods"].tool_def
    )

    assert "presentation" not in compare.parameters_json_schema["properties"]
    assert "date_filter_type" in compare.parameters_json_schema["properties"]
    # Reads with UI data keep it, and their tool has no prepare hook.
    assert tools["list_accounts"].prepare is None
    assert (
        "presentation"
        in tools["list_accounts"].tool_def.parameters_json_schema["properties"]
    )
