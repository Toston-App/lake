"""The result envelope every chat/MCP tool returns, and the handler contract.

``{"success": true, "data": ...}`` or ``{"success": false, "error": "..."}``.
Paged list tools add ``page``, ``has_more`` and ``next_page`` beside ``data``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.tools import LIST_LIMIT

Result = dict[str, Any]
# (result, action type recorded for the in-app chat)
Outcome = tuple[Result, str | None]


@dataclass(frozen=True)
class ToolContext:
    """What a handler may need besides its arguments."""

    made_from: str


# A tool's implementation: parsed arguments (None for argument-less tools).
Handler = Callable[[Any, AsyncSession, models.User, ToolContext], Awaitable[Outcome]]


def ok(data: Any) -> Result:
    return {"success": True, "data": jsonable_encoder(data)}


def err(message: str) -> Result:
    return {"success": False, "error": message}


def page_window(page: int) -> dict[str, int]:
    """``skip``/``limit`` for a page, fetching one extra row to detect more."""
    return {"skip": (page - 1) * LIST_LIMIT, "limit": LIST_LIMIT + 1}


def paged(rows: list, page: int) -> Result:
    """One page of ``rows`` (fetched with ``page_window``)."""
    has_more = len(rows) > LIST_LIMIT
    result = ok(rows[:LIST_LIMIT])
    result.update(
        page=page, has_more=has_more, next_page=page + 1 if has_more else None
    )
    return result
