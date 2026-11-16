"""The MCP Server object: tools, resources and prompts for authenticated users.

Authentication happens in BearerAuthMiddleware, which stores the user id in the
ASGI scope. The handler here refuses to run without it, so there is no
unauthenticated fallback.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.core.tiers import Tier
from app.db.session import async_session
from app.mcp_server import resources as mcp_resources
from app.mcp_server.instructions import server_instructions
from app.mcp_server.limits import enforce_mcp_limits
from app.mcp_server.tools import (
    MCP_TOOLS,
    RESULT_SCHEMA,
    call_tool,
    enabled_tools,
    input_schema,
)
from app.services.entitlements import get_user_tier
from app.services.errors import ServiceError
from app.services.mcp_audit import record_call
from app.services.profile import language_of
from app.services.rate_limits import RateLimitedError
from app.utilities.wide_events import enrich_event

logger = logging.getLogger(__name__)

server: Server = Server("toston", instructions=server_instructions())


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


def _action_id(result: dict, arguments: Any) -> str | None:
    """The pending action a call staged, or the one it confirmed or cancelled."""
    action_id = result.get("action_id")
    if action_id is None and isinstance(arguments, dict):
        action_id = arguments.get("action_id")
    return str(action_id)[:64] if action_id is not None else None


@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=tool.name,
            title=tool.name.replace("_", " ").title(),
            description=tool.description,
            inputSchema=input_schema(tool),
            outputSchema=RESULT_SCHEMA,
            annotations=types.ToolAnnotations(
                readOnlyHint=tool.read_only,
                destructiveHint=tool.destructive,
                openWorldHint=False,
            ),
        )
        for tool in enabled_tools()
    ]


@dataclass(frozen=True)
class _Outcome:
    """A call's response, and what the audit log records about it."""

    response: types.CallToolResult
    # completed | failed | confirmation_required | refused
    status: str
    # Why a call was refused. Failed calls record no message: tool errors can
    # quote the user's data.
    reason: str | None = None
    action_id: str | None = None


def _status_of(result: dict) -> str:
    if not result.get("success"):
        return "failed"
    if result.get("status") == "confirmation_required":
        return "confirmation_required"
    return "completed"


def _refused(text: str, reason: str) -> _Outcome:
    return _Outcome(_error_result(text), "refused", reason=reason)


def _authenticated() -> tuple[Any, dict, int] | None:
    """The request, its auth state and the user id, or None when missing."""
    try:
        request = server.request_context.request
        state = request.scope["state"]
        return request, state, state["toston_user_id"]
    except (LookupError, AttributeError, KeyError, TypeError):
        # Never fall back to anything unauthenticated.
        logger.error("MCP request without an authenticated user")
        return None


_REFUSALS: dict[str, dict[str, str]] = {
    "en": {
        "read_only_token": "This token is read-only.",
        "read_only_account": (
            "AI apps can only read your data: change this in Clever under "
            "Settings > AI connections."
        ),
        "account_not_found": "Account not found.",
        "internal_error": "The tool failed. Please try again.",
    },
    "es": {
        "read_only_token": "Este token es de solo lectura.",
        "read_only_account": (
            "Las apps de IA solo pueden leer tus datos: cámbialo en Clever en "
            "Ajustes > Conexiones de IA."
        ),
        "account_not_found": "No se encontró la cuenta.",
        "internal_error": "La herramienta falló. Inténtalo de nuevo.",
    },
}


def _requested_language(arguments: Any) -> str | None:
    if isinstance(arguments, dict) and arguments.get("language") in _REFUSALS:
        return arguments["language"]
    return None


@server.call_tool()
async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    auth = _authenticated()
    if auth is None:
        return _error_result("Not authenticated.")
    request, state, user_id = auth

    enrich_event(
        request, operation={"type": "mcp_tool", "tool_name": name}, user={"id": user_id}
    )
    started = time.monotonic()
    try:
        outcome = await _run(name, arguments, user_id, state)
    except Exception:
        # Still audited; the client gets no internal details.
        logger.exception("MCP tool %s failed", name)
        language = _requested_language(arguments) or "en"
        outcome = _Outcome(
            _error_result(_REFUSALS[language]["internal_error"]),
            "failed",
            reason="internal_error",
        )
    await record_call(
        user_id=user_id,
        client=state.get("toston_client"),
        tool_name=name,
        status=outcome.status,
        duration_ms=int((time.monotonic() - started) * 1000),
        action_id=outcome.action_id,
        error=outcome.reason,
    )
    return outcome.response


async def _run(
    name: str, arguments: dict[str, Any], user_id: int, state: dict
) -> _Outcome:
    # Only a personal access token can be read-only; anything else may write.
    tool = MCP_TOOLS.get(name)
    requested = _requested_language(arguments)
    if state.get("toston_token_scope", "write") != "write" and (
        tool is None or not tool.read_only
    ):
        text = _REFUSALS[requested or "en"]["read_only_token"]
        return _refused(text, "read_only_token")

    async with async_session() as db:
        user, tier = await _load_user_and_tier(db, user_id)
        if user is None:
            text = _REFUSALS[requested or "en"]["account_not_found"]
            return _refused(text, "account_not_found")
        language = requested or language_of(user)
        if user.mcp_read_only and (tool is None or not tool.read_only):
            return _refused(
                _REFUSALS[language]["read_only_account"], "read_only_account"
            )
        try:
            await enforce_mcp_limits(db, user, tier)
        except ServiceError as exc:
            text = exc.message
            if isinstance(exc, RateLimitedError):
                text += f" Retry after {exc.retry_after} seconds."
            return _refused(text, type(exc).__name__)
        result = await call_tool(name, arguments, db, user)

    response = types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, default=str))],
        structuredContent=result,
        isError=not result.get("success", False),
    )
    return _Outcome(
        response, _status_of(result), action_id=_action_id(result, arguments)
    )


# -- resources and prompts ------------------------------------------------------


@server.list_resources()
async def _list_resources() -> list[types.Resource]:
    return mcp_resources.list_resources()


@server.read_resource()
async def _read_resource(uri: Any) -> list[ReadResourceContents]:
    auth = _authenticated()
    if auth is None:
        raise PermissionError("Not authenticated.")
    _request, state, user_id = auth
    resource = mcp_resources.RESOURCES.get(str(uri))
    if resource is None:
        raise ValueError(f"Unknown resource: {uri}")
    started = time.monotonic()
    outcome = await _run(resource.tool_name, {}, user_id, state)
    await record_call(
        user_id=user_id,
        client=state.get("toston_client"),
        tool_name=f"resource:{resource.key}",
        status=outcome.status,
        duration_ms=int((time.monotonic() - started) * 1000),
        error=outcome.reason,
    )
    if outcome.response.isError:
        raise ValueError(outcome.response.content[0].text)
    return [
        ReadResourceContents(
            content=json.dumps(outcome.response.structuredContent, default=str),
            mime_type="application/json",
        )
    ]


@server.list_prompts()
async def _list_prompts() -> list[types.Prompt]:
    return mcp_resources.list_prompts()


@server.get_prompt()
async def _get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    return mcp_resources.get_prompt(name, arguments)
