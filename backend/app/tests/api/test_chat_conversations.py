from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from app.ai import agent
from app.api.api_v1.endpoints import chat
from app.schemas.chat import ChatConfirmRequest
from app.utilities.redis import PendingActionPersistenceError
from fastapi import HTTPException
from starlette.requests import Request


@pytest.fixture(scope="session", autouse=True)
def setup_database() -> None:
    """These endpoint-unit tests do not require the integration database."""


def _request(path: str = "/api/v1/chat") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )


def test_api_validation_preserves_safe_client_ids() -> None:
    assert chat._require_valid_conversation_id("conversation_01-test") == (
        "conversation_01-test"
    )


@pytest.mark.parametrize("conversation_id", ["", "bad/id", "a" * 129])
def test_api_validation_rejects_malformed_ids(conversation_id: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        chat._require_valid_conversation_id(conversation_id)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_active_send_returns_conflict_instead_of_overwriting() -> None:
    with patch.object(
        chat, "acquire_conversation_lock", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(HTTPException) as exc_info:
            await chat._acquire_chat_lock(1, "busy-conversation")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"reason": "conversation_busy"}


@pytest.mark.asyncio
async def test_lock_backend_failure_is_recoverable_service_error() -> None:
    with patch.object(
        chat,
        "acquire_conversation_lock",
        new=AsyncMock(side_effect=RuntimeError("redis unavailable")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await chat._acquire_chat_lock(1, "conversation")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {"reason": "conversation_lock_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "confirm"])
async def test_delete_and_confirmation_return_409_during_active_send(
    operation: str,
) -> None:
    user = SimpleNamespace(id=1)
    with patch.object(
        chat, "acquire_conversation_lock", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(HTTPException) as exc_info:
            if operation == "delete":
                await chat.remove_conversation(
                    conversation_id="busy-conversation",
                    current_user=user,
                )
            else:
                await chat.confirm_chat_action(
                    request=_request(),
                    conversation_id="busy-conversation",
                    body=ChatConfirmRequest(action_id="action"),
                    db=AsyncMock(),
                    current_user=user,
                )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"reason": "conversation_busy"}


@pytest.mark.asyncio
async def test_confirmation_releases_lock_after_execute_tool_failure() -> None:
    release = AsyncMock(return_value=True)
    with (
        patch.object(
            chat, "acquire_conversation_lock", new=AsyncMock(return_value="token")
        ),
        patch.object(chat, "release_conversation_lock", new=release),
        patch.object(chat, "_enforce_quota", new=AsyncMock()),
        patch.object(
            chat,
            "get_pending_action",
            new=AsyncMock(
                return_value={
                    "conversation_id": "conversation",
                    "tool_name": "create_expense",
                    "tool_args": {},
                    "response_language": "en",
                }
            ),
        ),
        patch.object(chat, "merge_confirm_tool_args", return_value={}),
        patch.object(
            chat,
            "execute_tool",
            new=AsyncMock(side_effect=RuntimeError("tool failed")),
        ),
        pytest.raises(RuntimeError, match="tool failed"),
    ):
        await chat.confirm_chat_action(
            request=_request(),
            conversation_id="conversation",
            body=ChatConfirmRequest(action_id="action"),
            db=AsyncMock(),
            current_user=SimpleNamespace(id=1),
        )

    release.assert_awaited_once_with(1, "conversation", "token")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["save", "upsert"])
async def test_confirmation_releases_lock_after_persistence_failure(
    failure_at: str,
) -> None:
    release = AsyncMock(return_value=True)
    save = AsyncMock(return_value=failure_at != "save")
    upsert = AsyncMock(
        side_effect=RuntimeError("upsert failed") if failure_at == "upsert" else None
    )
    with (
        patch.object(
            chat, "acquire_conversation_lock", new=AsyncMock(return_value="token")
        ),
        patch.object(chat, "release_conversation_lock", new=release),
        patch.object(chat, "_enforce_quota", new=AsyncMock()),
        patch.object(
            chat,
            "get_pending_action",
            new=AsyncMock(
                return_value={
                    "conversation_id": "conversation",
                    "tool_name": "create_expense",
                    "tool_args": {},
                    "response_language": "en",
                }
            ),
        ),
        patch.object(chat, "merge_confirm_tool_args", return_value={}),
        patch.object(
            chat,
            "execute_tool",
            new=AsyncMock(return_value=({"success": True}, None)),
        ),
        patch.object(chat, "delete_pending_action", new=AsyncMock(return_value=True)),
        patch.object(chat, "record_tool_call", new=AsyncMock()),
        patch.object(chat, "finalize_turn", new=AsyncMock()),
        patch.object(
            chat,
            "get_conversation",
            new=AsyncMock(return_value={"messages": [], "title": "Test"}),
        ),
        patch.object(chat, "load_model_messages", return_value=[]),
        patch.object(chat, "append_confirmation_to_model_history", return_value=[]),
        patch.object(chat, "public_ui_messages", return_value=[]),
        patch.object(chat, "serialize_model_messages", return_value=[]),
        patch.object(chat, "save_conversation", new=save),
        patch.object(chat, "upsert_conversation", new=upsert),
    ):
        expected_error = HTTPException if failure_at == "save" else RuntimeError
        with pytest.raises(expected_error) as exc_info:
            await chat.confirm_chat_action(
                request=_request(),
                conversation_id="conversation",
                body=ChatConfirmRequest(action_id="action"),
                db=AsyncMock(),
                current_user=SimpleNamespace(id=1),
            )

    if failure_at == "save":
        assert exc_info.value.status_code == 503
        upsert.assert_not_awaited()
    else:
        assert str(exc_info.value) == "upsert failed"
        upsert.assert_awaited_once()
    release.assert_awaited_once_with(1, "conversation", "token")


@pytest.mark.asyncio
async def test_stream_setup_failure_releases_lock_before_handoff() -> None:
    request = _request()
    adapter = SimpleNamespace(conversation_id="setup-failure", messages=[])
    release = AsyncMock(return_value=True)
    with (
        patch.object(request, "json", new=AsyncMock(return_value={})),
        patch.object(chat, "get_pydantic_agent", return_value=object()),
        patch.object(
            chat.VercelAIAdapter,
            "from_request",
            new=AsyncMock(return_value=adapter),
        ),
        patch.object(chat, "latest_user_request", return_value=[]),
        patch.object(
            chat, "acquire_conversation_lock", new=AsyncMock(return_value="token")
        ),
        patch.object(chat, "release_conversation_lock", new=release),
        patch.object(
            chat,
            "reserve_chat_turn",
            new=AsyncMock(side_effect=RuntimeError("setup failed")),
        ),
        pytest.raises(RuntimeError, match="setup failed"),
    ):
        await chat.chat_vercel(
            request=request,
            db=AsyncMock(),
            current_user=SimpleNamespace(id=1),
        )

    release.assert_awaited_once_with(1, "setup-failure", "token")
    assert request.state.chat_stream_lock is None


@pytest.mark.asyncio
async def test_failed_pending_write_cannot_render_an_approval_card() -> None:
    deps = SimpleNamespace(
        db=AsyncMock(),
        user=SimpleNamespace(id=1),
        conversation_id="conversation",
        turn_id=10,
        had_pending_confirmation=False,
    )
    ctx = SimpleNamespace(deps=deps)

    with (
        patch.object(
            agent,
            "build_pending_preview",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            agent,
            "store_pending_action",
            new=AsyncMock(
                side_effect=PendingActionPersistenceError("redis unavailable")
            ),
        ),
        pytest.raises(PendingActionPersistenceError),
    ):
        await agent._defer_for_confirmation(
            ctx,
            "create_expense",
            {"amount": 10},
            "en",
        )

    assert deps.had_pending_confirmation is False
