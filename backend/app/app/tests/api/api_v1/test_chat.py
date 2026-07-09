from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat_tools import execute_tool
from app.ai.render_components import (
    RENDER_COMPONENT_IDS,
    render_catalog_prompt,
    resolve_render_detail,
)
from app.core.config import settings
from app.schemas.chat import ChatAction, chat_action_detail


def test_resolve_render_detail_pairs_component_with_action():
    actions = [
        ChatAction(type="accounts_listed", detail={"items": [{"id": 1}]}),
        ChatAction(type="summary_fetched", detail={"period_net": 100}),
    ]
    directive = resolve_render_detail("period_summary", actions)
    assert directive is not None
    assert directive.component == "period_summary"
    assert directive.detail == {"period_net": 100}


def test_resolve_render_detail_rejects_unknown_component():
    assert resolve_render_detail("does_not_exist", []) is None


def test_render_catalog_prompt_lists_all_components():
    prompt = render_catalog_prompt()
    for component_id in RENDER_COMPONENT_IDS:
        assert component_id in prompt


@pytest.mark.asyncio
async def test_execute_tool_search_web_mocked():
    with patch.object(settings, "TAVILY_API_KEY", "tvly-test"):
        with patch(
            "app.ai.chat_tools.search_web",
            new_callable=AsyncMock,
            return_value={
                "query": "USD MXN rate",
                "answer": "18.5",
                "results": [{"title": "Banxico", "url": "https://example.com"}],
            },
        ):
            result, action_type = await execute_tool(
                "search_web",
                {"query": "USD MXN rate", "topic": "finance"},
                AsyncMock(),
                MagicMock(),
            )
    assert result["success"] is True
    assert action_type == "web_searched"
    assert result["data"]["answer"] == "18.5"


@pytest.mark.asyncio
async def test_execute_tool_search_web_requires_api_key():
    with patch.object(settings, "TAVILY_API_KEY", None):
        result, action_type = await execute_tool(
            "search_web",
            {"query": "test"},
            AsyncMock(),
            MagicMock(),
        )
    assert action_type is None
    assert result["success"] is False


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
    body = response.json()
    assert "profile" in body["reply"].lower() or body["actions"]
    assert body["ui_message"] is not None
    assert body["ui_message"]["role"] == "assistant"


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
