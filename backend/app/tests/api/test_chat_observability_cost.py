from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.ai import agent
from app.models.chat_observability import ChatModelPricing, ChatObsLLMCall, ChatObsTurn
from app.models.user import User
from app.utilities import observability
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class _SessionFactory:
    """Route observability's dedicated sessionmaker onto the test savepoint."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_SessionFactory":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.fixture
def obs_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncSession:
    monkeypatch.setattr(observability, "async_session", _SessionFactory(db_session))
    return db_session


def test_build_model_requests_openrouter_usage_accounting() -> None:
    model = agent.build_model("openai/test-model")
    assert model.settings is not None
    assert model.settings["openrouter_usage"]["include"] is True


def test_extract_cost_usd_prefers_openrouter_billed_cost() -> None:
    response = SimpleNamespace(
        provider_details={"cost": 0.024},
        cost=lambda: SimpleNamespace(total_price=9.99),
    )
    assert observability.extract_cost_usd(response) == pytest.approx(0.024)


def test_extract_cost_usd_falls_back_to_genai_prices() -> None:
    response = SimpleNamespace(
        provider_details={},
        cost=lambda: SimpleNamespace(total_price="0.044"),
    )
    assert observability.extract_cost_usd(response) == pytest.approx(0.044)


def test_extract_cost_usd_missing_is_none() -> None:
    response = SimpleNamespace(
        provider_details=None, cost=lambda: (_ for _ in ()).throw(LookupError())
    )
    assert observability.extract_cost_usd(response) is None


def test_extract_cost_usd_rejects_negative() -> None:
    response = SimpleNamespace(provider_details={"cost": -1}, cost=None)
    assert observability.extract_cost_usd(response) is None


async def test_obs_after_model_passes_billed_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = AsyncMock()
    monkeypatch.setattr(agent, "record_llm_call", recorded)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100_000, output_tokens=20_000, cache_read_tokens=0
        ),
        model_name="openai/gpt-5.6-luna",
        provider_details={"cost": 0.044},
        finish_reason="stop",
        tool_calls=[],
    )
    ctx = SimpleNamespace(deps=SimpleNamespace(turn_id=7), run_step=1)

    await agent._obs_after_model(ctx, request_context=None, response=response)

    recorded.assert_awaited_once()
    assert recorded.await_args.kwargs["cost_usd"] == pytest.approx(0.044)
    assert recorded.await_args.kwargs["prompt_tokens"] == 100_000
    assert recorded.await_args.kwargs["completion_tokens"] == 20_000
    assert recorded.await_args.kwargs["total_tokens"] == 120_000


async def test_finalize_turn_rolls_up_llm_call_costs(
    obs_session: AsyncSession, test_user: User
) -> None:
    turn_id = await observability.start_turn(
        conversation_id="conv-cost",
        user_id=test_user.id,
        user_message="hello",
        model="openai/gpt-5.6-luna",
    )
    assert turn_id is not None
    await observability.record_llm_call(
        turn_id=turn_id,
        round_index=0,
        model="openai/gpt-5.6-luna",
        prompt_tokens=80_000,
        completion_tokens=10_000,
        total_tokens=90_000,
        cost_usd=0.028,
    )
    await observability.record_llm_call(
        turn_id=turn_id,
        round_index=1,
        model="openai/gpt-5.6-luna",
        prompt_tokens=20_000,
        completion_tokens=10_000,
        total_tokens=30_000,
        cost_usd=0.016,
    )
    await observability.finalize_turn(
        turn_id,
        status="ok",
        total_prompt_tokens=100_000,
        total_completion_tokens=20_000,
        total_tokens=120_000,
    )

    turn = await obs_session.get(ChatObsTurn, turn_id)
    assert turn is not None
    assert turn.total_tokens == 120_000
    assert float(turn.total_cost_usd) == pytest.approx(0.044)


async def test_record_llm_call_estimates_cost_from_pricing(
    obs_session: AsyncSession, test_user: User
) -> None:
    obs_session.add(
        ChatModelPricing(
            model="openai/gpt-5.6-luna",
            provider="openrouter",
            input_price_per_1m=Decimal("0.20"),
            output_price_per_1m=Decimal("1.20"),
            cached_input_price_per_1m=Decimal("0.02"),
        )
    )
    await obs_session.commit()

    turn_id = await observability.start_turn(
        conversation_id="conv-pricing",
        user_id=test_user.id,
        user_message="hello",
        model="gpt-5.6-luna",
    )
    assert turn_id is not None
    await observability.record_llm_call(
        turn_id=turn_id,
        round_index=0,
        model="gpt-5.6-luna",
        prompt_tokens=100_000,
        completion_tokens=20_000,
        total_tokens=120_000,
    )

    calls = (
        (
            await obs_session.execute(
                select(ChatObsLLMCall).where(ChatObsLLMCall.turn_id == turn_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(calls) == 1
    # 100k * $0.20/1M + 20k * $1.20/1M = $0.02 + $0.024 = $0.044
    assert float(calls[0].cost_usd) == pytest.approx(0.044)
