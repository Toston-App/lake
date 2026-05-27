import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_agent import ChatAgent, ChatTurnResult
from app.ai.chat_tools import execute_tool, is_destructive_tool, requires_confirmation
from app.core.config import settings
from app.schemas.chat import ChatAction


@pytest.mark.asyncio
async def test_chat_disabled_returns_503(
    client: AsyncClient, normal_user_token_headers: dict[str, str]
):
    with patch.object(settings, "CHAT_ENABLED", False):
        response = await client.post(
            f"{settings.API_V1_STR}/chat/messages",
            headers=normal_user_token_headers,
            json={"message": "Hello"},
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_send_message_mocked_agent(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
):
    mock_turn = ChatTurnResult(
        reply="Your balance looks good.",
        actions=[ChatAction(type="summary_fetched", detail={"period_net": 100})],
        new_messages=[
            {"role": "user", "content": "How am I doing?"},
            {"role": "assistant", "content": "Your balance looks good."},
        ],
    )
    with patch(
        "app.api.api_v1.endpoints.chat.get_chat_agent"
    ) as mock_get_agent:
        mock_agent = MagicMock(spec=ChatAgent)
        mock_agent.run_turn = AsyncMock(return_value=mock_turn)
        mock_get_agent.return_value = mock_agent
        with patch(
            "app.api.api_v1.endpoints.chat.save_conversation",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch(
                "app.api.api_v1.endpoints.chat.get_conversation",
                new_callable=AsyncMock,
                return_value=None,
            ):
                response = await client.post(
                    f"{settings.API_V1_STR}/chat/messages",
                    headers=normal_user_token_headers,
                    json={"message": "How am I doing?"},
                )
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Your balance looks good."
    assert data["conversation_id"]
    assert len(data["actions"]) == 1


@pytest.mark.asyncio
async def test_execute_tool_list_accounts(
    async_get_db: AsyncSession,
    normal_user_token_headers: dict[str, str],
    client: AsyncClient,
):
    from app import crud

    me = await client.get(
        f"{settings.API_V1_STR}/users/me",
        headers=normal_user_token_headers,
    )
    assert me.status_code == 200

    users = await crud.user.get_by_email(async_get_db, email=settings.EMAIL_TEST_USER)
    result, action_type = await execute_tool(
        "list_accounts", {}, async_get_db, users
    )
    assert result["success"] is True
    assert action_type == "accounts_listed"
    assert isinstance(result["data"], list)


@pytest.mark.asyncio
async def test_destructive_tools_flagged():
    assert is_destructive_tool("delete_expense", {"id": 1})
    assert is_destructive_tool("manage_account", {"action": "delete", "id": 1})
    assert not is_destructive_tool("create_expense", {"amount": 10})
    assert not is_destructive_tool("manage_account", {"action": "create", "data": {}})


@pytest.mark.asyncio
async def test_transaction_writes_require_confirmation():
    assert requires_confirmation("create_expense", {"amount": 10})
    assert requires_confirmation("update_income", {"id": 1, "data": {"amount": 5}})
    assert requires_confirmation("create_transfer", {"amount": 1, "from_acc": 1, "to_acc": 2})
    assert not requires_confirmation("list_accounts", {})
    assert not requires_confirmation("get_period_summary", {"date_filter_type": "month", "date": "2026-05"})


@pytest.mark.asyncio
async def test_confirm_pending_action(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    async_get_db: AsyncSession,
):
    from app import crud

    user = await crud.user.get_by_email(async_get_db, email=settings.EMAIL_TEST_USER)
    conversation_id = "test-conv-confirm"
    action_id = "test-action-123"

    with patch(
        "app.api.api_v1.endpoints.chat.get_pending_action",
        new_callable=AsyncMock,
        return_value={
            "user_id": user.id,
            "conversation_id": conversation_id,
            "tool_name": "get_user_profile",
            "tool_args": {},
            "preview": {},
        },
    ):
        with patch(
            "app.api.api_v1.endpoints.chat.delete_pending_action",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch(
                "app.api.api_v1.endpoints.chat.get_conversation",
                new_callable=AsyncMock,
                return_value={"messages": [], "title": "Test"},
            ):
                with patch(
                    "app.api.api_v1.endpoints.chat.save_conversation",
                    new_callable=AsyncMock,
                    return_value=True,
                ):
                    response = await client.post(
                        f"{settings.API_V1_STR}/chat/conversations/{conversation_id}/confirm",
                        headers=normal_user_token_headers,
                        json={"action_id": action_id},
                    )
    assert response.status_code == 200
    assert "profile" in response.json()["reply"].lower() or response.json()["actions"]
