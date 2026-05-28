from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.chat_agent import ChatAgent, _TurnState
from app.ai.chat_tools import (
    UI_RENDERED_REPLY_HINT,
    append_ui_rendered_reply_hint,
    build_system_prompt,
)
from app.schemas.chat import ChatAction


def test_append_ui_rendered_reply_hint_for_list_action():
    messages: list[dict] = [{"role": "system", "content": "test"}]
    assert append_ui_rendered_reply_hint(messages, ["accounts_listed"]) is True
    assert messages[-1] == {"role": "developer", "content": UI_RENDERED_REPLY_HINT}


def test_append_ui_rendered_reply_hint_skips_mutation_action():
    messages: list[dict] = [{"role": "system", "content": "test"}]
    assert append_ui_rendered_reply_hint(messages, ["expense_created"]) is False
    assert len(messages) == 1


def test_build_system_prompt_mentions_ui_rendered_actions():
    prompt = build_system_prompt({"accounts": [], "categories": [], "places": []})
    assert "client shows from actions" in prompt


@pytest.mark.asyncio
async def test_handle_tool_calls_appends_ui_rendered_hint():
    agent = ChatAgent(client=AsyncMock())
    state = _TurnState(
        messages=[{"role": "system", "content": "test"}],
        actions=[],
        tool_steps=[],
        new_messages=[],
    )
    tool_calls = [
        {
            "id": "tc1",
            "type": "function",
            "function": {"name": "list_accounts", "arguments": "{}"},
        }
    ]

    with patch(
        "app.ai.chat_agent.execute_tool",
        new_callable=AsyncMock,
        return_value=(
            {"success": True, "data": [{"id": 1, "name": "BBVA"}]},
            "accounts_listed",
        ),
    ):
        events = []
        async for event in agent._handle_tool_calls(
            state,
            tool_calls,
            None,
            db=AsyncMock(),
            user=MagicMock(),
            conversation_id="conv-1",
            confirmed_action=None,
            emit_steps=False,
        ):
            events.append(event)

    assert events == []
    assert state.actions == [
        ChatAction(type="accounts_listed", detail={"items": [{"id": 1, "name": "BBVA"}]})
    ]
    assert any(m.get("role") == "developer" for m in state.messages)
    assert state.new_messages == []
