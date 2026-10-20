"""Axiom telemetry for the WhatsApp bot.

Every inbound WhatsApp message produces exactly one ``whatsapp.message`` event
with what the user sent, what the LLM did with it, and every reply the bot
sent back. Events are emitted directly (never sampled) so LLM quality can be
measured from the full population of messages.

A transaction spans two messages: the text the LLM parses, and the later
confirm/cancel button tap. The button event carries a ``feedback`` block that
points back at the parse event (``source_interaction_id``) and repeats the
model, prompt version and user input, so confirmation rate can be grouped by
model or prompt without a join.

Useful APL queries::

    ['cleverbill'] | where event_type == "whatsapp.message" and action == "parse_transaction"
      | summarize count() by outcome, ['llm.model_used']

    ['cleverbill'] | where event_type == "whatsapp.message" and isnotnull(['feedback.verdict'])
      | summarize count() by ['feedback.verdict'], ['feedback.prompt_version']
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from app.utilities.axiom import log_event

logger = logging.getLogger(__name__)

EVENT_TYPE = "whatsapp.message"

_current: ContextVar[WhatsAppInteraction | None] = ContextVar(
    "whatsapp_interaction", default=None
)


class WhatsAppInteraction:
    """Accumulates the telemetry for one inbound WhatsApp message."""

    def __init__(
        self,
        *,
        wa_message_id: str | None,
        from_hash: str,
        message_type: str,
        request_id: str | None = None,
    ):
        self.id = str(uuid.uuid4())
        self._start = time.monotonic()
        self.event: dict[str, Any] = {
            "_time": datetime.now(timezone.utc).isoformat(),
            "event_type": EVENT_TYPE,
            "interaction_id": self.id,
            "request_id": request_id,
            "wa_message_id": wa_message_id,
            "from_hash": from_hash,
            "message_type": message_type,
            "replies": [],
        }

    def set(self, **fields: Any) -> None:
        """Set top-level event fields (``action``, ``outcome``, ``user`` ...)."""
        self.event.update(fields)

    def setdefault(self, key: str, value: Any) -> None:
        self.event.setdefault(key, value)

    def record_reply(self, reply: dict[str, Any]) -> None:
        self.event["replies"].append(reply)

    def record_error(self, error: BaseException) -> None:
        self.event["outcome"] = "error"
        self.event["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "stack_trace": "".join(traceback.format_exception(error)),
        }

    def finalize(self) -> dict[str, Any]:
        self.event["duration_ms"] = round((time.monotonic() - self._start) * 1000, 2)
        self.event.setdefault("outcome", "unknown")
        self.event["replies_count"] = len(self.event["replies"])
        self.event["replies_failed"] = sum(
            1 for r in self.event["replies"] if r.get("status") != "success"
        )
        return self.event


@asynccontextmanager
async def track_interaction(**kwargs: Any) -> AsyncIterator[WhatsAppInteraction]:
    """Track one inbound message; replies sent inside the block are recorded.

    The event is emitted on exit, including when the block raises (the
    exception is recorded and re-raised).
    """
    interaction = WhatsAppInteraction(**kwargs)
    token = _current.set(interaction)
    try:
        yield interaction
    except Exception as e:
        interaction.record_error(e)
        raise
    finally:
        _current.reset(token)
        await _emit(interaction.finalize())


def current_interaction() -> WhatsAppInteraction | None:
    return _current.get()


def record_outgoing(
    message_type: str,
    content: dict[str, Any],
    result: dict[str, Any],
    duration_ms: float,
) -> None:
    """Record a message sent to the user on the active interaction, if any."""
    interaction = _current.get()
    if interaction is None:
        return

    reply: dict[str, Any] = {
        "type": message_type,
        "status": result.get("status"),
        "duration_ms": duration_ms,
        **summarize_content(message_type, content),
    }
    response = result.get("response") or {}
    if result.get("status") == "success":
        messages = response.get("messages") or [{}]
        reply["wa_message_id"] = messages[0].get("id")
    else:
        error = response.get("error") if isinstance(response, dict) else None
        reply["error"] = error or response
    interaction.record_reply(reply)


def summarize_content(message_type: str, content: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Graph API message payload into the text the user actually saw."""
    if message_type == "text":
        return {"text": content.get("body")}
    if message_type == "reaction":
        return {"emoji": content.get("emoji"), "reacted_to": content.get("message_id")}
    if message_type == "interactive":
        summary: dict[str, Any] = {
            "interactive_type": content.get("type"),
            "text": (content.get("body") or {}).get("text"),
        }
        action = content.get("action") or {}
        if content.get("type") == "button":
            summary["options"] = [
                b.get("reply", {}).get("title") for b in action.get("buttons", [])
            ]
        elif content.get("type") == "list":
            summary["options"] = [
                row.get("title")
                for section in action.get("sections", [])
                for row in section.get("rows", [])
            ]
        return summary
    return {}


async def _emit(event: dict[str, Any]) -> None:
    try:
        await asyncio.wait_for(log_event(event), timeout=0.25)
    except Exception:
        logger.exception("WhatsApp telemetry emission failed")
