from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from app.ai import agent
from app.api.api_v1.endpoints import chat
from app.schemas.chat import ChatConfirmRequest
from app.utilities.redis import PendingActionPersistenceError
from fastapi import HTTPException
from pydantic_ai.usage import RequestUsage
from starlette.requests import Request


@pytest.fixture(scope="session", autouse=True)
def setup_database() -> None:
    """These endpoint-unit tests do not require the integration database."""


@pytest.fixture(autouse=True)
def allow_rate_limit():
    """Keep the Redis-backed chat rate limiter out of endpoint-unit tests."""
    with patch.object(chat, "hit_rate_limit", new=AsyncMock(return_value=(1, 60))):
        yield


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


def _stream_patches(run_stream, finalize, release):
    """Patch ``chat_vercel`` collaborators so its metered stream can be driven."""
    from app.core.tiers import Tier

    adapter = SimpleNamespace(
        conversation_id="metered",
        messages=[],
        run_stream=run_stream,
        streaming_response=lambda stream: stream,
    )
    quota = SimpleNamespace(allowed=True, tier=Tier.free, reason=None)

    async def passthrough(events):
        async for event in events:
            yield event

    return (
        patch.object(chat, "get_pydantic_agent", return_value=object()),
        patch.object(
            chat.VercelAIAdapter, "from_request", new=AsyncMock(return_value=adapter)
        ),
        patch.object(chat, "latest_user_request", return_value=[]),
        patch.object(
            chat, "acquire_conversation_lock", new=AsyncMock(return_value="token")
        ),
        patch.object(chat, "release_conversation_lock", new=release),
        patch.object(
            chat, "reserve_chat_turn", new=AsyncMock(return_value=(quota, 42))
        ),
        patch.object(chat, "get_conversation", new=AsyncMock(return_value=None)),
        patch.object(chat, "load_model_messages", return_value=[]),
        patch.object(chat, "deferred_question_results", return_value=None),
        patch.object(chat, "build_toolset", return_value=object()),
        patch.object(chat, "build_obs_hooks", return_value=object()),
        patch.object(chat, "public_chat_stream", new=passthrough),
        patch.object(chat, "finalize_turn", new=finalize),
    )


async def _open_stream(run_stream, finalize, release):
    request = _request()
    patches = _stream_patches(run_stream, finalize, release)
    with patch.object(request, "json", new=AsyncMock(return_value={})):
        for p in patches:
            p.start()
        try:
            stream = await chat.chat_vercel(
                request=request,
                db=AsyncMock(),
                current_user=SimpleNamespace(id=1, email=None),
            )
        except BaseException:
            for p in reversed(patches):
                p.stop()
            raise
    return stream, patches


@pytest.mark.asyncio
async def test_client_disconnect_bills_turn_as_interrupted() -> None:
    finalize = AsyncMock()
    release = AsyncMock(return_value=True)

    def run_stream(**kwargs):
        # Pydantic AI accumulates into the run usage the endpoint passes in.
        usage = kwargs["usage"]

        async def events():
            usage.incr(RequestUsage(input_tokens=1200, output_tokens=300))
            yield "text-delta"
            yield "finish"

        return events()

    stream, patches = await _open_stream(run_stream, finalize, release)
    try:
        assert await stream.__anext__() == "text-delta"
        # Client closes the connection before on_complete records the turn.
        await stream.aclose()
    finally:
        for p in reversed(patches):
            p.stop()

    finalize.assert_awaited_once()
    kwargs = finalize.await_args.kwargs
    assert kwargs["status"] == "interrupted"
    assert kwargs["error"] == "stream_interrupted"
    assert kwargs["total_tokens"] == 1500
    release.assert_awaited_once_with(1, "metered", "token")


@pytest.mark.asyncio
async def test_failed_run_refunds_message_but_bills_tokens() -> None:
    finalize = AsyncMock()
    release = AsyncMock(return_value=True)

    def run_stream(**kwargs):
        usage = kwargs["usage"]

        async def events():
            usage.incr(RequestUsage(input_tokens=900, output_tokens=100))
            yield "error"

        return events()

    stream, patches = await _open_stream(run_stream, finalize, release)
    try:
        assert [event async for event in stream] == ["error"]
    finally:
        for p in reversed(patches):
            p.stop()

    kwargs = finalize.await_args.kwargs
    assert kwargs["status"] == "error"
    assert kwargs["error"] == "run_failed"
    assert kwargs["total_tokens"] == 1000


@pytest.mark.asyncio
async def test_failed_confirmation_does_not_refund_the_proposing_turn() -> None:
    finalize = AsyncMock()
    with (
        patch.object(
            chat, "acquire_conversation_lock", new=AsyncMock(return_value="token")
        ),
        patch.object(
            chat, "release_conversation_lock", new=AsyncMock(return_value=True)
        ),
        patch.object(chat, "_enforce_quota", new=AsyncMock()),
        patch.object(
            chat,
            "get_pending_action",
            new=AsyncMock(
                return_value={
                    "conversation_id": "conversation",
                    "tool_name": "delete_expense",
                    "tool_args": {"id": 1},
                    "response_language": "en",
                    "turn_id": 42,
                }
            ),
        ),
        patch.object(
            chat,
            "execute_tool",
            new=AsyncMock(return_value=({"success": False, "error": "nope"}, None)),
        ),
        patch.object(chat, "delete_pending_action", new=AsyncMock(return_value=True)),
        patch.object(chat, "record_tool_call", new=AsyncMock()),
        patch.object(chat, "finalize_turn", new=finalize),
        patch.object(chat, "get_conversation", new=AsyncMock(return_value=None)),
        patch.object(chat, "load_model_messages", return_value=[]),
        patch.object(chat, "append_confirmation_to_model_history", return_value=[]),
        patch.object(chat, "public_ui_messages", return_value=[]),
        patch.object(chat, "serialize_model_messages", return_value=[]),
        patch.object(chat, "save_conversation", new=AsyncMock(return_value=True)),
        patch.object(chat, "upsert_conversation", new=AsyncMock()),
    ):
        await chat.confirm_chat_action(
            request=_request(),
            conversation_id="conversation",
            body=ChatConfirmRequest(action_id="action", tool_args={"id": 999}),
            db=AsyncMock(),
            current_user=SimpleNamespace(id=1),
        )

    finalize.assert_awaited_once_with(
        42, status="ok", pending_confirmation=False, error="nope"
    )


@pytest.mark.asyncio
async def test_chat_rate_limit_rejects_over_limit_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat.settings, "CHAT_RATE_LIMIT_PER_MINUTE", 10)
    with patch.object(chat, "hit_rate_limit", new=AsyncMock(return_value=(11, 30))):
        with pytest.raises(HTTPException) as exc_info:
            await chat._enforce_chat_rate_limit(1)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "30"}
    assert exc_info.value.detail == {"reason": "rate_limited", "retry_after": 30}


@pytest.mark.asyncio
async def test_chat_rate_limit_fails_closed_when_redis_is_down() -> None:
    with patch.object(
        chat, "hit_rate_limit", new=AsyncMock(side_effect=RuntimeError("down"))
    ):
        with pytest.raises(HTTPException) as exc_info:
            await chat._enforce_chat_rate_limit(1)

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_chat_rate_limit_window_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat.settings, "CHAT_RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(chat.settings, "CHAT_RATE_LIMIT_PER_DAY", 0)
    hit = AsyncMock(return_value=(10_000, 1))
    with patch.object(chat, "hit_rate_limit", new=hit):
        await chat._enforce_chat_rate_limit(1)

    hit.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_runs_with_turn_budget_and_fresh_run_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat.settings, "CHAT_MAX_TOOL_ROUNDS", 4)
    captured: dict = {}
    budget = object()

    def run_stream(**kwargs):
        captured.update(kwargs)

        async def events():
            yield "finish"

        return events()

    with patch.object(chat, "build_turn_budget", return_value=budget):
        stream, patches = await _open_stream(run_stream, AsyncMock(), AsyncMock())
        try:
            [event async for event in stream]
        finally:
            for p in reversed(patches):
                p.stop()

    limits = captured["usage_limits"]
    assert limits.request_limit == 5
    # The token budget is enforced before each call, never mid-stream.
    assert limits.total_tokens_limit is None
    assert budget in captured["capabilities"]
    assert captured["usage"].requests == 0


def test_chat_model_caps_output_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent.settings, "CHAT_MAX_OUTPUT_TOKENS", 1234)

    assert agent.build_model().settings["max_tokens"] == 1234
