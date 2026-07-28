"""Public/private presentation helpers for the chat transport.

Pydantic AI model messages are the private orchestration history.  They may
contain reasoning, tool arguments, and tool results and must never be returned
to the chat client.  The public projection contains only parts the product UI
deliberately renders.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from pydantic_ai.ui.vercel_ai.response_types import (
    BaseChunk,
    DataChunk,
    FileChunk,
    SourceDocumentChunk,
    SourceUrlChunk,
)

_ui_messages_adapter = TypeAdapter(list[UIMessage])

_PUBLIC_STREAM_TYPES = frozenset(
    {
        "abort",
        "done",
        "error",
        "file",
        "finish",
        "finish-step",
        "message-metadata",
        "source-document",
        "source-url",
        "start",
        "start-step",
        "text-delta",
        "text-end",
        "text-start",
    }
)
_PUBLIC_STORED_PART_TYPES = frozenset({"file", "source-document", "source-url", "text"})
_METADATA_CHUNK_TYPES: dict[str, type[BaseChunk]] = {
    "file": FileChunk,
    "source-document": SourceDocumentChunk,
    "source-url": SourceUrlChunk,
}


def is_public_chat_chunk(chunk: BaseChunk) -> bool:
    """Return whether an SSE chunk is part of the public chat contract."""
    chunk_type = str(getattr(chunk, "type", ""))
    return chunk_type.startswith("data-") or chunk_type in _PUBLIC_STREAM_TYPES


async def public_chat_stream(
    stream: AsyncIterator[BaseChunk],
) -> AsyncIterator[BaseChunk]:
    """Drop tool and reasoning events before the stream reaches the client."""
    async for chunk in stream:
        if is_public_chat_chunk(chunk):
            yield chunk


def public_ui_messages(messages: Sequence[ModelMessage]) -> list[dict[str, Any]]:
    """Project private model history into renderable Vercel UI messages."""
    dumped = VercelAIAdapter.dump_messages(messages, sdk_version=6)
    public: list[dict[str, Any]] = []

    for message in dumped:
        if message.role not in {"user", "assistant"}:
            continue

        raw = message.model_dump(mode="json", by_alias=True, exclude_none=True)
        parts = raw.get("parts", [])
        visible_parts = [
            part
            for part in parts
            if isinstance(part, dict)
            and (
                str(part.get("type", "")).startswith("data-")
                or part.get("type") in _PUBLIC_STORED_PART_TYPES
            )
        ]
        if not visible_parts:
            continue
        raw["parts"] = visible_parts
        public.append(raw)

    return public


def serialize_model_messages(
    messages: Sequence[ModelMessage],
) -> list[dict[str, Any]]:
    """Serialize private history into Redis-safe JSON values."""
    value = ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")
    return list(value)


def latest_user_request(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """Select the newest user turn from either compact or legacy requests."""
    for message in reversed(messages):
        if isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in message.parts
        ):
            return [message]
    return []


def load_model_messages(conversation: dict[str, Any] | None) -> list[ModelMessage]:
    """Load private history, lazily upgrading legacy public-only conversations."""
    if not conversation:
        return []

    stored_model_messages = conversation.get("model_messages")
    if isinstance(stored_model_messages, list):
        try:
            loaded = ModelMessagesTypeAdapter.validate_python(stored_model_messages)
            return _restore_metadata_chunks(loaded)
        except ValidationError:
            # Fall back to the public compatibility history below.
            pass

    result: list[ModelMessage] = []
    for message in conversation.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if isinstance(message.get("parts"), list):
            try:
                ui_message = _ui_messages_adapter.validate_python([message])
            except ValidationError:
                continue
            result.extend(VercelAIAdapter.load_messages(ui_message))
            continue

        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        if message.get("role") == "user":
            result.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif message.get("role") == "assistant":
            result.append(ModelResponse(parts=[TextPart(content=content)]))

    return result


def _restore_metadata_chunks(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """Restore typed UI chunks erased by generic metadata deserialization."""
    restored: list[ModelMessage] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            restored.append(message)
            continue

        request_parts = []
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or not isinstance(
                part.metadata, list
            ):
                request_parts.append(part)
                continue

            metadata: list[Any] = []
            for item in part.metadata:
                if not isinstance(item, dict):
                    metadata.append(item)
                    continue
                item_type = str(item.get("type", ""))
                chunk_type = (
                    DataChunk
                    if item_type.startswith("data-")
                    else _METADATA_CHUNK_TYPES.get(item_type)
                )
                if chunk_type is None:
                    metadata.append(item)
                    continue
                try:
                    metadata.append(chunk_type.model_validate(item))
                except ValidationError:
                    metadata.append(item)
            request_parts.append(replace(part, metadata=metadata))
        restored.append(replace(message, parts=request_parts))

    return restored


def append_confirmation_to_model_history(
    history: Sequence[ModelMessage],
    *,
    tool_name: str,
    reply: str,
) -> list[ModelMessage]:
    """Record the user-visible confirmation outcome in private model context."""
    return [
        *history,
        ModelRequest(parts=[UserPromptPart(content=f"[Confirmed] {tool_name}")]),
        ModelResponse(parts=[TextPart(content=reply)]),
    ]
