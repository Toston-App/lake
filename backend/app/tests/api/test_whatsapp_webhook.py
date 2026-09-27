"""Tests for the WhatsApp webhook's Axiom interaction events."""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.ai.whatsapp_parser import PROMPT_VERSION, ParseResult
from app.api.api_v1.endpoints import whatsapp as endpoint
from app.models.user import User
from app.utilities import whatsapp as whatsapp_utils
from app.utilities import whatsapp_telemetry
from app.utilities.encryption import hash_sha256
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

SENDER = "5215512345678"
URL = "/api/v1/whatsapp/webhook"


class _FakeGraphClient:
    """Stands in for httpx.AsyncClient when talking to the Graph API."""

    sent: list[dict[str, Any]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeGraphClient.sent.append(json)
        return _FakeResponse()


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"messages": [{"id": f"wamid.out.{len(_FakeGraphClient.sent)}"}]}


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    async def _capture(event):
        captured.append(event)

    monkeypatch.setattr(whatsapp_telemetry, "log_event", _capture)
    _FakeGraphClient.sent = []
    monkeypatch.setattr(whatsapp_utils.httpx, "AsyncClient", _FakeGraphClient)
    return captured


@pytest.fixture
async def linked_user(db_session: AsyncSession, test_user: User) -> User:
    test_user.phone = hash_sha256(f"+{SENDER}")
    await db_session.flush()
    return test_user


def _payload(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messages": [{"from": SENDER, **message}]}}]}],
    }


def _text(body: str, message_id: str = "wamid.in.1") -> dict[str, Any]:
    return _payload({"id": message_id, "type": "text", "text": {"body": body}})


def _button(button_id: str, title: str, message_id: str = "wamid.in.2") -> dict[str, Any]:
    return _payload({
        "id": message_id,
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": title}},
    })


async def test_text_message_event_has_input_llm_and_replies(
    client: AsyncClient, linked_user: User, events, monkeypatch: pytest.MonkeyPatch
):
    trace = {"model_used": "openai/gpt-4o-mini", "prompt_version": PROMPT_VERSION, "raw_output": "{...}", "adjustments": []}
    transaction = {
        "id": "cena-ab", "type": "expense", "amount": 200.0, "date": date(2026, 9, 26),
        "category": None, "subcategory": None, "place": None, "description": "Cena", "account": None,
    }
    monkeypatch.setattr(endpoint.whatsapp_parser, "parse_message", AsyncMock(return_value=ParseResult(transaction, trace)))
    store = AsyncMock(return_value=True)
    monkeypatch.setattr(endpoint, "store_transaction", store)

    response = await client.post(URL, json=_text("gasté 200 en cena ayer"))

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    [event] = events
    assert event["event_type"] == "whatsapp.message"
    assert event["action"] == "parse_transaction"
    assert event["outcome"] == "awaiting_confirmation"
    assert event["input"] == {"text": "gasté 200 en cena ayer", "length": 22}
    assert event["user"]["id"] == linked_user.id
    assert event["llm"]["model_used"] == "openai/gpt-4o-mini"
    assert event["llm"]["raw_output"] == "{...}"
    assert event["transaction"]["amount"] == 200.0
    assert event["transaction"]["date"] == "2026-09-26"

    reaction, confirmation = event["replies"]
    assert reaction == {
        "type": "reaction", "status": "success", "duration_ms": reaction["duration_ms"],
        "emoji": "⏳", "reacted_to": "wamid.in.1", "wa_message_id": "wamid.out.1",
    }
    assert confirmation["interactive_type"] == "button"
    assert "Confirma los datos de tu *gasto*" in confirmation["text"]
    assert confirmation["options"] == ["❌ Cancelar", "✅ Confirmar"]

    # The cached transaction links the later confirm/cancel back to this event
    cached = store.await_args.kwargs["transaction_data"]["telemetry"]
    assert cached["interaction_id"] == event["interaction_id"]
    assert cached["input_text"] == "gasté 200 en cena ayer"


async def test_parse_failure_records_reason_and_help_message(
    client: AsyncClient, linked_user: User, events, monkeypatch: pytest.MonkeyPatch
):
    trace = {"failure_reason": "invalid_json", "raw_output": "nope"}
    monkeypatch.setattr(endpoint.whatsapp_parser, "parse_message", AsyncMock(return_value=ParseResult({}, trace)))

    await client.post(URL, json=_text("asdf"))

    [event] = events
    assert event["outcome"] == "parse_failed"
    assert event["llm"]["failure_reason"] == "invalid_json"
    assert [r.get("emoji") for r in event["replies"][:2]] == ["⏳", "😵‍💫"]
    assert event["replies"][2]["text"].startswith("❌ No pude entender tu mensaje")


async def test_cancel_records_feedback_linked_to_parse(
    client: AsyncClient, linked_user: User, events, monkeypatch: pytest.MonkeyPatch
):
    cached = {
        "user_id": str(linked_user.id),
        "data": {
            "id": "cena-ab", "type": "expense", "amount": 200.0, "message_to_react": "wamid.in.1",
            "telemetry": {
                "interaction_id": "parse-interaction", "input_text": "gasté 200 en cena",
                "model_used": "openai/gpt-4o-mini", "prompt_version": "abc123", "parsed_at": 0,
            },
        },
    }
    monkeypatch.setattr(endpoint, "get_transaction", AsyncMock(return_value=cached))
    monkeypatch.setattr(endpoint, "delete_transaction", AsyncMock(return_value=True))

    await client.post(URL, json=_button("cancel_cena-ab", "❌ Cancelar"))

    [event] = events
    assert event["action"] == "cancel_transaction"
    assert event["outcome"] == "cancelled"
    assert event["input"] == {"reply_id": "cancel_cena-ab", "reply_title": "❌ Cancelar"}
    feedback = event["feedback"]
    assert feedback["verdict"] == "cancelled"
    assert feedback["source_interaction_id"] == "parse-interaction"
    assert feedback["source_input_text"] == "gasté 200 en cena"
    assert feedback["model_used"] == "openai/gpt-4o-mini"
    assert feedback["prompt_version"] == "abc123"


async def test_confirm_of_expired_transaction(
    client: AsyncClient, linked_user: User, events, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(endpoint, "get_transaction", AsyncMock(return_value=None))

    await client.post(URL, json=_button("confirm_gone-xy", "✅ Confirmar"))

    [event] = events
    assert event["outcome"] == "expired"
    assert event["feedback"] == {"verdict": "expired", "transaction_id": "gone-xy"}


async def test_unregistered_number(client: AsyncClient, events):
    await client.post(URL, json=_text("hola"))

    [event] = events
    assert event["action"] == "unregistered_user"
    assert event["from_hash"] == hash_sha256(f"+{SENDER}")
    assert event["replies"][0]["text"].startswith("👋 ¡Hola!")


async def test_unsupported_message_type_is_ignored(client: AsyncClient, linked_user: User, events):
    response = await client.post(URL, json=_payload({"id": "wamid.img", "type": "image", "image": {"id": "media-1"}}))

    assert response.json() == {"status": "success"}
    [event] = events
    assert event["message_type"] == "image"
    assert event["outcome"] == "ignored"
    assert event["replies"] == []


async def test_handler_exception_is_recorded_and_batch_continues(
    client: AsyncClient, linked_user: User, events, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(endpoint.whatsapp_parser, "parse_message", AsyncMock(side_effect=RuntimeError("kaboom")))
    payload = _text("gasté 100")
    payload["entry"][0]["changes"][0]["value"]["messages"].append(
        {"from": SENDER, "id": "wamid.img", "type": "image", "image": {}}
    )

    response = await client.post(URL, json=payload)

    assert response.json() == {"status": "error"}
    failed, next_message = events
    assert failed["outcome"] == "error"
    assert failed["error"]["type"] == "RuntimeError"
    assert failed["error"]["message"] == "kaboom"
    # The next message is still handled (its outcome depends on the rolled-back
    # test session, so only check that it ran).
    assert next_message["wa_message_id"] == "wamid.img"
    assert next_message["outcome"] != "error"
