"""Per-turn budget and billing, driven through a real Pydantic AI run.

These use ``FunctionModel`` rather than a fake event stream so they exercise
the library paths the endpoint relies on: per-request model settings from the
budget hook, and the run usage that failed turns are billed from.
"""

from datetime import date
from types import SimpleNamespace

import pytest
from app.ai import agent
from app.core.tiers import Tier
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage, RunUsage


@pytest.fixture(scope="session", autouse=True)
def setup_database() -> None:
    """These tests do not require the integration database."""


def _tool_loop_agent(
    tool_choices: list[object], *, input_tokens: int
) -> Agent[None, str]:
    """An agent whose model calls a tool until tools are turned off."""

    def respond(_messages: list, info: AgentInfo) -> ModelResponse:
        tool_choice = (info.model_settings or {}).get("tool_choice")
        tool_choices.append(tool_choice)
        usage = RequestUsage(input_tokens=input_tokens, output_tokens=500)
        if tool_choice == "none":
            return ModelResponse(parts=[TextPart("Here is what I found.")], usage=usage)
        return ModelResponse(parts=[ToolCallPart("lookup", {})], usage=usage)

    loop_agent = Agent(FunctionModel(respond), output_type=str)

    @loop_agent.tool_plain
    def lookup() -> str:
        return "more data"

    return loop_agent


@pytest.mark.parametrize(
    ("requests", "total_tokens", "spent"),
    [
        (0, 0, False),
        (7, 99_999, False),
        (8, 0, True),
        (0, 100_000, True),
    ],
)
def test_turn_budget_spent_by_tool_rounds_or_tokens(
    monkeypatch: pytest.MonkeyPatch, requests: int, total_tokens: int, spent: bool
) -> None:
    monkeypatch.setattr(agent.settings, "CHAT_MAX_TOOL_ROUNDS", 8)
    monkeypatch.setattr(agent.settings, "CHAT_MAX_TOKENS_PER_TURN", 100_000)
    usage = RunUsage(requests=requests, input_tokens=total_tokens)

    assert agent.turn_budget_spent(usage) is spent


async def test_token_budget_ends_the_turn_with_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent.settings, "CHAT_MAX_TOOL_ROUNDS", 8)
    monkeypatch.setattr(agent.settings, "CHAT_MAX_TOKENS_PER_TURN", 100_000)
    tool_choices: list[object] = []
    run_usage = RunUsage()

    result = await _tool_loop_agent(tool_choices, input_tokens=60_000).run(
        "How much did I spend?",
        usage_limits=agent.build_usage_limits(),
        usage=run_usage,
        capabilities=[agent.build_turn_budget()],
    )

    # 60.5k after the first call, 121k after the second: the third must answer.
    assert result.output == "Here is what I found."
    assert tool_choices == [None, None, "none"]
    assert run_usage.requests == 3
    assert run_usage.total_tokens == 181_500


async def test_last_tool_round_must_answer_instead_of_hitting_request_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent.settings, "CHAT_MAX_TOOL_ROUNDS", 2)
    monkeypatch.setattr(agent.settings, "CHAT_MAX_TOKENS_PER_TURN", 10_000_000)
    tool_choices: list[object] = []

    result = await _tool_loop_agent(tool_choices, input_tokens=1_000).run(
        "Compare every month",
        usage_limits=agent.build_usage_limits(),
        capabilities=[agent.build_turn_budget()],
    )

    assert result.output == "Here is what I found."
    assert tool_choices == [None, None, "none"]


def test_usage_limits_have_no_mid_stream_token_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent.settings, "CHAT_MAX_TOOL_ROUNDS", 4)

    limits = agent.build_usage_limits()

    assert limits.request_limit == 5
    assert limits.total_tokens_limit is None


async def test_run_usage_counts_a_call_that_fails_mid_stream() -> None:
    """The endpoint bills failed turns from this usage, not from a hook."""

    async def stream(_messages: list, _info: AgentInfo):
        yield "The full "
        yield "answer"
        raise RuntimeError("provider dropped the stream")

    failing_agent = Agent(FunctionModel(stream_function=stream), output_type=str)
    run_usage = RunUsage()

    with pytest.raises(RuntimeError, match="dropped"):
        async with failing_agent.run_stream_events("hi", usage=run_usage) as events:
            async for _ in events:
                pass

    assert run_usage.requests == 1
    assert run_usage.output_tokens > 0


async def test_instructions_tell_the_model_to_answer_when_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def bootstrap(_db, _user):
        return {}

    monkeypatch.setattr(agent, "load_bootstrap_context", bootstrap)
    monkeypatch.setattr(agent, "build_system_prompt", lambda *a, **k: "BASE")
    monkeypatch.setattr(agent.settings, "CHAT_MAX_TOOL_ROUNDS", 8)
    deps = SimpleNamespace(
        db=None, user=None, tier=Tier.free, local_date=date(2026, 1, 1), time_zone="UTC"
    )

    fresh = await agent.chat_instructions(SimpleNamespace(deps=deps, usage=RunUsage()))
    spent = await agent.chat_instructions(
        SimpleNamespace(deps=deps, usage=RunUsage(requests=8))
    )

    assert fresh == "BASE"
    assert spent.startswith("BASE\n\n")
    assert "tools are unavailable" in spent
