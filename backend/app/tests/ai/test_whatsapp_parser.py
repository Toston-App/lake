"""Tests for the LLM trace the WhatsApp parser returns alongside its result."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.ai.whatsapp_parser import PROMPT_VERSION, WhatsAppParser

CATEGORIES = [
    {"id": 1, "name": "Alimentación", "subcategories": [{"id": 10, "name": "Restaurantes"}]},
    {"id": 2, "name": "Transporte", "subcategories": [{"id": 20, "name": "Gasolina"}]},
]


def _completion(content: str, model: str = "openai/gpt-4o-mini") -> SimpleNamespace:
    return SimpleNamespace(
        id="gen-123",
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=900, completion_tokens=80, total_tokens=980),
    )


def _parser(create: AsyncMock) -> WhatsAppParser:
    parser = WhatsAppParser(api_key="test", model="openai/gpt-4o-mini", fallback_models=["anthropic/claude-3-haiku"])
    parser.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return parser


async def test_successful_parse_records_llm_trace():
    ai_result = {
        "type": "expense",
        "amount": 200,
        "date": "2026-09-26",
        "category": {"id": 1, "name": "Alimentación"},
        "subcategory": {"id": 10, "name": "Restaurantes"},
        "description": "Cena",
        "id": "cena",
    }
    raw = json.dumps(ai_result)
    parser = _parser(AsyncMock(return_value=_completion(raw, model="anthropic/claude-3-haiku")))

    result = await parser.parse_message("gasté 200 en cena ayer", categories=CATEGORIES)

    assert result.ok
    assert result.transaction["amount"] == 200.0
    assert result.transaction["subcategory_id"] == 10
    trace = result.trace
    assert trace["model_requested"] == "openai/gpt-4o-mini"
    assert trace["model_used"] == "anthropic/claude-3-haiku"  # fallback actually served it
    assert trace["prompt_version"] == PROMPT_VERSION
    assert trace["raw_output"] == raw
    assert trace["ai_result"] == ai_result
    assert trace["usage"] == {"prompt_tokens": 900, "completion_tokens": 80, "total_tokens": 980}
    assert trace["generation_id"] == "gen-123"
    assert trace["context_items"] == {"categories": 2, "places": 0, "accounts": 0}
    assert trace["latency_ms"] >= 0
    assert trace["adjustments"] == []
    assert "failure_reason" not in trace


async def test_adjustments_are_recorded_when_llm_picks_mismatched_subcategory():
    ai_result = {
        "type": "expense",
        "amount": 350,
        "category": {"id": 1, "name": "Alimentación"},
        "subcategory": {"id": 20, "name": "Gasolina"},  # belongs to category 2
    }
    parser = _parser(AsyncMock(return_value=_completion(json.dumps(ai_result))))

    result = await parser.parse_message("350 gasolina", categories=CATEGORIES)

    assert result.ok
    assert result.transaction["category_id"] is None
    assert result.transaction["subcategory_id"] is None
    assert result.trace["adjustments"] == ["subcategory_not_in_category", "category_dropped_without_subcategory"]


async def test_default_account_applied_is_recorded():
    ai_result = {"type": "expense", "amount": 50, "account": None}
    parser = _parser(AsyncMock(return_value=_completion(json.dumps(ai_result))))

    result = await parser.parse_message("50 café", default_account=SimpleNamespace(id=7, name="BBVA"))

    assert result.transaction["account_id"] == 7
    assert "default_account_applied" in result.trace["adjustments"]


async def test_invalid_json_is_a_failure_with_raw_output_kept():
    parser = _parser(AsyncMock(return_value=_completion("not json")))

    result = await parser.parse_message("hola")

    assert not result.ok
    assert result.trace["failure_reason"] == "invalid_json"
    assert result.trace["raw_output"] == "not json"


async def test_llm_error_is_recorded():
    parser = _parser(AsyncMock(side_effect=RuntimeError("boom")))

    result = await parser.parse_message("gasté 100")

    assert not result.ok
    assert result.trace["failure_reason"] == "llm_error"
    assert "boom" in result.trace["error"]


async def test_validation_failure_is_recorded():
    parser = _parser(AsyncMock(return_value=_completion(json.dumps({"type": "expense", "amount": 0}))))

    result = await parser.parse_message("hola")

    assert not result.ok
    assert result.trace["failure_reason"] == "validation_failed"


async def test_no_client_and_empty_message():
    parser = WhatsAppParser(api_key=None)
    assert (await parser.parse_message("gasté 100")).trace["failure_reason"] == "no_client"
    assert (await parser.parse_message("   ")).trace["failure_reason"] == "empty_message"
