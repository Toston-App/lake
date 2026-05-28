import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_agent import ChatAgent, ChatStreamEvent, ChatTurnResult
from app.schemas.chat import ToolStep
from app.ai.chat_tools import (
    _transaction_update_data,
    execute_tool,
    is_destructive_tool,
    requires_confirmation,
)
from app.core.config import settings
from app.schemas.chat import ChatAction, chat_action_detail


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


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event_type = "message"
        data_line = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data_line = line[6:]
        if data_line is not None:
            events.append((event_type, json.loads(data_line)))
    return events


@pytest.mark.asyncio
async def test_send_message_stream_mocked_agent(
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

    async def mock_stream_turn(*_args, **_kwargs):
        yield ChatStreamEvent(type="token", delta="Your ")
        yield ChatStreamEvent(type="token", delta="balance")
        yield ChatStreamEvent(type="tool_step", tool_step=ToolStep(
            tool_name="get_period_summary",
            status="completed",
            label="Fetching period summary",
        ))
        yield ChatStreamEvent(type="done", result=mock_turn)

    with patch(
        "app.api.api_v1.endpoints.chat.get_chat_agent"
    ) as mock_get_agent:
        mock_agent = MagicMock(spec=ChatAgent)
        mock_agent.stream_turn = mock_stream_turn
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
                async with client.stream(
                    "POST",
                    f"{settings.API_V1_STR}/chat/messages/stream",
                    headers=normal_user_token_headers,
                    json={"message": "How am I doing?"},
                ) as response:
                    assert response.status_code == 200
                    body = await response.aread()
    text = body.decode()
    events = _parse_sse_events(text)
    types = [e[0] for e in events]
    assert types[0] == "meta"
    assert "conversation_id" in events[0][1]
    assert "token" in types
    assert "tool_step" in types
    assert types[-1] == "done"
    done_data = events[-1][1]
    assert done_data["reply"] == "Your balance looks good."
    assert len(done_data["actions"]) == 1


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
    assert chat_action_detail(result["data"]) == {"items": result["data"]}
    assert chat_action_detail([]) == {"items": []}


def test_transaction_update_data_coerces_date_string():
    out = _transaction_update_data({"date": "2026-05-27", "amount": 10.0})
    assert out["date"] == date(2026, 5, 27)
    assert out["amount"] == 10.0


@pytest.mark.asyncio
async def test_execute_tool_error_returns_tuple(
    async_get_db: AsyncSession,
):
    from app import crud

    user = await crud.user.get_by_email(async_get_db, email=settings.EMAIL_TEST_USER)
    result, action_type = await execute_tool(
        "manage_account",
        {"action": "update", "id": 999999999, "data": {"name": "x"}},
        async_get_db,
        user,
    )
    assert action_type is None
    assert isinstance(result, dict)
    assert result["success"] is False
    assert "not found" in result["error"].lower()


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


@pytest.mark.asyncio
async def test_execute_tool_delete_income_uses_shared_side_effects(
    async_get_db: AsyncSession,
):
    from app import crud, schemas
    from app.services import transactions as transaction_service

    user = await crud.user.get_by_email(async_get_db, email=settings.EMAIL_TEST_USER)
    account = await crud.account.create_with_owner(
        async_get_db,
        obj_in=schemas.AccountCreate(
            name="Chat Income Parity", initial_balance=0, color="#168FFF"
        ),
        owner_id=user.id,
    )
    category = await crud.category.create_with_owner(
        async_get_db,
        obj_in=schemas.CategoryCreate(
            name="Chat Income Category", color="#168FFF", is_income=True
        ),
        owner_id=user.id,
    )
    subcategory = await crud.subcategory.create_with_owner(
        async_get_db,
        obj_in=schemas.SubcategoryCreate(
            name="Chat Income Subcategory", category_id=category.id
        ),
        owner_id=user.id,
    )
    income = await transaction_service.create_income(
        async_get_db,
        user,
        schemas.IncomeCreate(
            amount=42,
            date="2026-05-27",
            account_id=account.id,
            subcategory_id=subcategory.id,
            made_from="Chat",
        ),
    )

    result, action_type = await execute_tool(
        "delete_income", {"id": income.id}, async_get_db, user, confirmed=True
    )

    assert result["success"] is True
    assert action_type == "income_deleted"
    await async_get_db.refresh(account)
    await async_get_db.refresh(category)
    await async_get_db.refresh(subcategory)
    assert account.total_incomes == 0
    assert account.current_balance == 0
    assert category.total == 0
    assert subcategory.total == 0


@pytest.mark.asyncio
async def test_execute_tool_delete_transfer_uses_shared_side_effects(
    async_get_db: AsyncSession,
):
    from app import crud, schemas
    from app.services import transactions as transaction_service

    user = await crud.user.get_by_email(async_get_db, email=settings.EMAIL_TEST_USER)
    source = await crud.account.create_with_owner(
        async_get_db,
        obj_in=schemas.AccountCreate(
            name="Chat Transfer Source", initial_balance=0, color="#168FFF"
        ),
        owner_id=user.id,
    )
    destination = await crud.account.create_with_owner(
        async_get_db,
        obj_in=schemas.AccountCreate(
            name="Chat Transfer Destination", initial_balance=0, color="#168FFF"
        ),
        owner_id=user.id,
    )
    transfer = await transaction_service.create_transfer(
        async_get_db,
        user,
        schemas.TransferCreate(
            amount=25,
            date="2026-05-27",
            from_acc=source.id,
            to_acc=destination.id,
        ),
    )

    result, action_type = await execute_tool(
        "delete_transfer", {"id": transfer.id}, async_get_db, user, confirmed=True
    )

    assert result["success"] is True
    assert action_type == "transfer_deleted"
    await async_get_db.refresh(source)
    await async_get_db.refresh(destination)
    assert source.total_transfers_out == 0
    assert source.current_balance == 0
    assert destination.total_transfers_in == 0
    assert destination.current_balance == 0
