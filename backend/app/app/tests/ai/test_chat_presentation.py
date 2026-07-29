from collections.abc import AsyncIterator

import pytest
from app.ai.chat_presentation import (
    append_confirmation_to_model_history,
    is_public_chat_chunk,
    latest_user_request,
    load_model_messages,
    public_chat_stream,
    public_ui_messages,
    serialize_model_messages,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai.response_types import (
    DataChunk,
    ReasoningStartChunk,
    SourceUrlChunk,
    TextStartChunk,
    ToolInputStartChunk,
    ToolOutputAvailableChunk,
)


def test_public_chunk_policy_hides_tools_and_reasoning():
    assert not is_public_chat_chunk(
        ToolInputStartChunk(toolCallId="call-1", toolName="get_user_profile")
    )
    assert not is_public_chat_chunk(
        ToolOutputAvailableChunk(toolCallId="call-1", output={"secret": True})
    )
    assert not is_public_chat_chunk(ReasoningStartChunk(id="reasoning-1"))


def test_public_chunk_policy_keeps_renderable_events():
    assert is_public_chat_chunk(TextStartChunk(id="text-1"))
    assert is_public_chat_chunk(
        DataChunk(type="data-accounts-list", data={"items": []})
    )
    assert is_public_chat_chunk(
        SourceUrlChunk(
            sourceId="source-1",
            url="https://example.com",
            title="Example",
        )
    )


@pytest.mark.asyncio
async def test_public_stream_preserves_data_from_hidden_tool():
    async def events() -> AsyncIterator:
        yield ToolInputStartChunk(toolCallId="call-1", toolName="list_accounts")
        yield ToolOutputAvailableChunk(toolCallId="call-1", output="Found 1 account.")
        yield DataChunk(type="data-accounts-list", data={"items": [{"id": 1}]})

    visible = [event async for event in public_chat_stream(events())]

    assert [event.type for event in visible] == ["data-accounts-list"]


@pytest.mark.asyncio
async def test_public_stream_suppresses_text_after_blocking_card():
    async def events() -> AsyncIterator:
        yield DataChunk(
            type="data-pending-confirmation",
            data={"action_id": "action-1"},
        )
        yield TextStartChunk(id="duplicate-text")

    visible = [event async for event in public_chat_stream(events())]

    assert [event.type for event in visible] == ["data-pending-confirmation"]


def _private_history():
    call_id = "call-1"
    return [
        ModelRequest(parts=[UserPromptPart(content="Show my accounts")]),
        ModelResponse(
            parts=[
                ThinkingPart(content="I should inspect the accounts."),
                ToolCallPart(
                    tool_name="list_accounts",
                    args={},
                    tool_call_id=call_id,
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="list_accounts",
                    content="Found 1 account.",
                    tool_call_id=call_id,
                    metadata=[
                        DataChunk(
                            type="data-accounts-list",
                            data={"items": [{"id": 1}]},
                        )
                    ],
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Here is your account.")]),
    ]


def test_public_history_strips_private_parts_but_keeps_tool_metadata():
    public = public_ui_messages(_private_history())
    all_parts = [part for message in public for part in message["parts"]]
    part_types = [part["type"] for part in all_parts]

    assert part_types == ["text", "data-accounts-list", "text"]
    assert not any(part_type.startswith("tool-") for part_type in part_types)
    assert "reasoning" not in part_types


def test_private_history_round_trips_with_tool_context():
    history = _private_history()
    serialized = serialize_model_messages(history)
    loaded = load_model_messages({"model_messages": serialized})

    assert serialize_model_messages(loaded) == serialized
    assert any(
        isinstance(part, ToolCallPart)
        for message in loaded
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    reloaded_public_types = [
        part["type"]
        for message in public_ui_messages(loaded)
        for part in message["parts"]
    ]
    assert "data-accounts-list" in reloaded_public_types


def test_legacy_public_history_is_upgraded():
    loaded = load_model_messages(
        {
            "messages": [
                {"role": "user", "content": "Hello"},
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "parts": [{"type": "text", "text": "Hi"}],
                },
            ]
        }
    )

    assert len(loaded) == 2
    assert isinstance(loaded[0], ModelRequest)
    assert isinstance(loaded[1], ModelResponse)


def test_latest_user_request_discards_client_owned_history():
    messages = [
        ModelRequest(parts=[UserPromptPart(content="Old question")]),
        ModelResponse(parts=[TextPart(content="Old answer")]),
        ModelRequest(parts=[UserPromptPart(content="Newest question")]),
    ]

    latest = latest_user_request(messages)

    assert len(latest) == 1
    assert isinstance(latest[0], ModelRequest)
    assert latest[0].parts[0].content == "Newest question"


def test_confirmation_updates_private_and_public_histories():
    updated = append_confirmation_to_model_history(
        _private_history(),
        tool_name="create_expense",
        reply="Done. Expense created.",
    )
    public = public_ui_messages(updated)

    assert public[-1]["parts"][0]["text"] == "Done. Expense created."
    assert not any(
        part.get("text", "").startswith("[Confirmed]")
        for message in public
        for part in message["parts"]
    )
    assert "model_messages" not in public[-1]


def test_public_history_suppresses_duplicate_text_after_question():
    history = [
        ModelRequest(parts=[UserPromptPart(content="Show spending")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="ask_user",
                    args={},
                    tool_call_id="question-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="ask_user",
                    content="Which period?",
                    tool_call_id="question-1",
                    metadata=[
                        DataChunk(
                            type="data-pending-question",
                            data={"question": "Which period?", "options": []},
                        )
                    ],
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Which period would you like?")]),
    ]

    public = public_ui_messages(history)
    texts = [
        part["text"]
        for message in public
        for part in message["parts"]
        if part["type"] == "text"
    ]

    assert texts == ["Show spending"]
