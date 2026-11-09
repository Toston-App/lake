"""The AI boundary of recurring detection: typed inputs, failure vs no answer."""

import json

from app.ai import recurring_detection as ai
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _model(answer, prompts: list[str] | None = None) -> FunctionModel:
    """`answer(items) -> output items`, or an exception to raise."""

    def fn(messages, info: AgentInfo) -> ModelResponse:
        content = next(
            part.content
            for message in reversed(messages)
            for part in getattr(message, "parts", [])
            if isinstance(part, UserPromptPart)
        )
        if prompts is not None:
            prompts.append(content)
        if isinstance(answer, Exception):
            raise answer
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name, {"items": answer(json.loads(content))}
                )
            ]
        )

    return FunctionModel(fn)


QUERIES = [
    ai.MerchantQuery(type="expense", text="netflix", place=None),
    ai.MerchantQuery(type="income", text="nómina", place="Acme"),
]
SUMMARY = ai.CandidateSummary(
    type="expense",
    merchant_name="Gym",
    category_name="Health",
    frequency="monthly",
    interval=1,
    occurrences=6,
    amount_min=29.99,
    amount_max=31.0,
)


async def test_merchant_payload_is_indexed_in_order():
    prompts: list[str] = []
    answer = _model(
        lambda items: [
            {"i": x["i"], "merchant_key": x["text"], "merchant_name": x["text"]}
            for x in items
        ],
        prompts,
    )
    stats = ai.AiStats()
    with ai.merchant_agent.override(model=answer):
        out = await ai.resolve_merchants(QUERIES, stats)

    assert prompts == [
        '[{"i": 0, "type": "expense", "text": "netflix", "place": null}, '
        '{"i": 1, "type": "income", "text": "nómina", "place": "Acme"}]'
    ]
    assert out is not None and sorted(out) == [0, 1]
    assert out[1].merchant_key == "nómina"
    assert (stats.calls, stats.errors) == (1, 0)


async def test_classify_payload_is_indexed_in_order():
    prompts: list[str] = []
    answer = _model(lambda items: [], prompts)
    with ai.classify_agent.override(model=answer):
        await ai.classify_candidates([SUMMARY], ai.AiStats())

    assert prompts == [
        '[{"i": 0, "type": "expense", "merchant_name": "Gym", '
        '"category_name": "Health", "frequency": "monthly", "interval": 1, '
        '"occurrences": 6, "amount_min": 29.99, "amount_max": 31.0}]'
    ]


async def test_failure_is_none_and_counted():
    stats = ai.AiStats()
    with ai.merchant_agent.override(model=_model(TimeoutError("slow"))):
        assert await ai.resolve_merchants(QUERIES, stats) is None
    with ai.classify_agent.override(model=_model(TimeoutError("slow"))):
        assert await ai.classify_candidates([SUMMARY], stats) is None
    assert (stats.calls, stats.errors) == (2, 2)
    assert stats.model == ai.model_name()


async def test_no_answers_is_not_a_failure():
    stats = ai.AiStats()
    with ai.classify_agent.override(model=_model(lambda items: [])):
        assert await ai.classify_candidates([SUMMARY], stats) == {}
    assert (stats.calls, stats.errors) == (1, 0)


async def test_no_items_makes_no_call():
    stats = ai.AiStats()
    assert await ai.resolve_merchants([], stats) == {}
    assert await ai.classify_candidates([], stats) == {}
    assert stats.calls == 0


async def test_unknown_indexes_are_dropped():
    answer = _model(
        lambda items: [
            {"i": i, "kind": "bill", "is_commitment": True, "confidence": 0.5}
            for i in (0, 7)
        ]
    )
    with ai.classify_agent.override(model=answer):
        out = await ai.classify_candidates([SUMMARY], ai.AiStats())
    assert out is not None and list(out) == [0]


async def test_habit_kind_is_reported_as_other():
    answer = _model(
        lambda items: [
            {"i": 0, "kind": "habit", "is_commitment": False, "confidence": 0.9}
        ]
    )
    with ai.classify_agent.override(model=answer):
        out = await ai.classify_candidates([SUMMARY], ai.AiStats())
    assert out is not None
    assert out[0].kind == "other"
    assert out[0].is_commitment is False
