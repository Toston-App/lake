import json
from unittest.mock import AsyncMock, patch

import pytest

from app.utilities import redis


@pytest.mark.asyncio
async def test_save_conversation_stores_preview_in_user_index():
    messages = [
        {
            "id": "message-1",
            "role": "assistant",
            "parts": [{"type": "text", "text": "Latest response"}],
        }
    ]

    with (
        patch.object(redis.r, "set", new_callable=AsyncMock),
        patch.object(redis.r, "expire", new_callable=AsyncMock),
        patch.object(redis.r, "hset", new_callable=AsyncMock) as hset,
    ):
        saved = await redis.save_conversation(
            "conversation-1",
            42,
            messages,
            "Budget",
            3600,
        )

    assert saved is True
    index_meta = json.loads(hset.await_args.args[2])
    assert index_meta["last_message_preview"] == "Latest response"


@pytest.mark.asyncio
async def test_list_user_conversations_reads_only_the_summary_index():
    entries = {
        "conversation-1": json.dumps(
            {
                "title": "Budget",
                "updated_at": "2026-08-05T00:00:00+00:00",
                "last_message_preview": "Latest response",
            }
        )
    }

    with (
        patch.object(
            redis.r,
            "hgetall",
            new_callable=AsyncMock,
            return_value=entries,
        ),
        patch.object(redis.r, "get", new_callable=AsyncMock) as get,
    ):
        conversations = await redis.list_user_conversations(42)

    get.assert_not_awaited()
    assert conversations == [
        {
            "id": "conversation-1",
            "title": "Budget",
            "updated_at": "2026-08-05T00:00:00+00:00",
            "last_message_preview": "Latest response",
        }
    ]
