"""The MCP Server object: tool listing and tool calls for authenticated users.

Authentication happens in BearerAuthMiddleware, which stores the user id in the
ASGI scope. The handler here refuses to run without it, so there is no
unauthenticated fallback.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.core.tiers import Tier
from app.db.session import async_session
from app.mcp_server.instructions import SERVER_INSTRUCTIONS
from app.mcp_server.limits import enforce_mcp_limits
from app.mcp_server.tools import MCP_TOOLS, call_tool, input_schema
from app.services.entitlements import get_user_tier
from app.services.errors import ServiceError
from app.services.rate_limits import RateLimitedError
from app.utilities.wide_events import enrich_event

logger = logging.getLogger(__name__)

server: Server = Server("toston", instructions=SERVER_INSTRUCTIONS)


async def _load_user_and_tier(
    db: AsyncSession, user_id: int
) -> tuple[models.User | None, Tier]:
    user = await crud.user.get(db, id=user_id)
    tier, _ = await get_user_tier(db, user_id)
    return user, tier


def _error_result(text: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], isError=True
    )


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=tool.name,
            title=tool.name.replace("_", " ").title(),
            description=tool.description,
            inputSchema=input_schema(tool),
            annotations=types.ToolAnnotations(
                readOnlyHint=tool.read_only,
                destructiveHint=tool.destructive,
                openWorldHint=False,
            ),
        )
        for tool in MCP_TOOLS.values()
    ]


@server.call_tool()
async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    try:
        request = server.request_context.request
        user_id = request.scope["state"]["toston_user_id"]
    except (LookupError, AttributeError, KeyError, TypeError):
        # Never fall back to anything unauthenticated.
        logger.error("MCP tool call without an authenticated user")
        return _error_result("Not authenticated.")

    enrich_event(
        request, operation={"type": "mcp_tool", "tool_name": name}, user={"id": user_id}
    )

    async with async_session() as db:
        user, tier = await _load_user_and_tier(db, user_id)
        if user is None:
            return _error_result("Account not found.")
        try:
            await enforce_mcp_limits(user, tier)
        except ServiceError as exc:
            text = exc.message
            if isinstance(exc, RateLimitedError):
                text += f" Retry after {exc.retry_after} seconds."
            return _error_result(text)
        result = await call_tool(name, arguments, db, user)

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, default=str))],
        structuredContent=result,
        isError=not result.get("success", False),
    )
