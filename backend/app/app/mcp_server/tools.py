"""MCP-facing adapter over ``ai.dispatch.execute_tool``; no transport.

The in-app chat already has a typed, ownership-checked tool dispatcher, and
``ai.confirmation`` holds the policy for risky and destructive operations. This
module exposes that dispatcher to external LLM hosts: a registry of tools with
JSON Schemas for their inputs, and ``call_tool``, which validates input and
applies the confirmation policy.

External hosts may auto-approve tool calls, and the user's LLM context can hold
prompt-injected text, so risky calls never run on the first call. They return a
preview plus an ``action_id``, and only an explicit ``confirm_action`` executes
them. Confirmation state lives in Redis, keyed by the action id and owned by one
user, and a claim is atomic so an action runs at most once.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from functools import cache
from typing import Any
from zoneinfo import available_timezones

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.confirmation import (
    MCP_CONVERSATION_ID,
    PREVIEW_OUTDATED,
    ConfirmEditRejected,
    PendingActionConversationMismatch,
    PendingActionNotFound,
    PreviewOutdated,
    ToolExecutionError,
    apply_untrusted_confirm_edits,
    claim_current_action,
    discard_action,
    requires_confirmation,
    stage_action,
)
from app.ai.dispatch import describe_validation_error, execute_tool
from app.ai.results import Result, err, ok
from app.ai.tool_specs import TOOL_SPECS, ToolSpec, apply_date_defaults
from app.ai.tools import TOOL_ARG_MODELS
from app.core.config import settings
from app.schemas.made_from import MADE_FROM_MCP
from app.services.errors import ServiceError
from app.services.profile import language_of
from app.services.recurring_schedule import local_today
from app.utilities.redis import CHAT_PENDING_TTL

# Toston's own messages (previews, approval prompts, this module's errors)
# are in English or Spanish: the client's ``language``, else the user's locale.
LANGUAGES = ("en", "es")
LANGUAGE_FIELD = "language"
_LANGUAGE_SCHEMA = {
    "type": "string",
    "enum": list(LANGUAGES),
    "description": (
        "Language of Toston's previews and messages: es (Spanish) or en "
        "(English). Defaults to the user's language in Toston."
    ),
}
CONFIRM_TOOL = "confirm_action"
CANCEL_TOOL = "cancel_action"
# Fields that only matter to Toston's own chat UI; MCP clients never see them.
_CHAT_ONLY_FIELDS = ("presentation", "response_language")
# MCP-only input on tools that default a date: the user's IANA time zone, so
# "today" is the user's today and not the server's.
TIME_ZONE_FIELD = "time_zone"
_TIME_ZONE_SCHEMA = {
    "type": "string",
    "description": (
        "The user's IANA time zone, e.g. Europe/Madrid or America/Bogota. "
        "Dates left empty default to today in this zone (UTC if omitted)."
    ),
}

# Every tool returns this envelope (``ai.results``); ``data`` varies by tool.
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "data": {"description": "The tool's result; its shape depends on the tool."},
        "error": {"type": "string", "description": "Why the call failed."},
        "status": {
            "type": "string",
            "enum": ["confirmation_required"],
            "description": "Set when the call was staged instead of run.",
        },
        "action_id": {"type": "string"},
        "expires_in_seconds": {"type": "integer"},
        "preview": {"type": "object"},
        "message": {"type": "string"},
        "next_step": {"type": "string"},
        "page": {"type": "integer"},
        "has_more": {"type": "boolean"},
        "next_page": {"type": ["integer", "null"]},
    },
    "required": ["success"],
}

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "not_found": "Action not found, expired, or already confirmed",
        "outdated": PREVIEW_OUTDATED,
        "next_step": (
            "Show this preview to the user. Call confirm_action with this "
            "action_id ONLY after the user explicitly approves; otherwise "
            "call cancel_action."
        ),
        "unknown_time_zone": (
            "Unknown time_zone {value!r}: use an IANA name such as Europe/Madrid."
        ),
        "unknown_language": "Unknown language {value!r}: use en or es.",
    },
    "es": {
        "not_found": "La acción no existe, venció o ya se confirmó",
        "outdated": (
            "El registro cambió después de mostrar esta vista previa. Pide la "
            "acción de nuevo para ver la versión actual."
        ),
        "next_step": (
            "Show this preview to the user in Spanish. Call confirm_action "
            "with this action_id ONLY after the user explicitly approves; "
            "otherwise call cancel_action."
        ),
        "unknown_time_zone": (
            "Zona horaria desconocida {value!r}: usa un nombre IANA como "
            "America/Mexico_City."
        ),
        "unknown_language": "Idioma desconocido {value!r}: usa en o es.",
    },
}


def message(language: str, key: str, **values: Any) -> str:
    return _MESSAGES[language][key].format(**values)


# (arguments, db, user, language)
Handler = Callable[[dict[str, Any], AsyncSession, models.User, str], Awaitable[Result]]
Precheck = Callable[[dict[str, Any]], str | None]


class ConfirmActionArgs(BaseModel):
    action_id: str = Field(
        ...,
        description=(
            "The action_id returned by a tool whose status was confirmation_required."
        ),
    )
    edits: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional field changes the user asked for before confirming, "
            'e.g. {"amount": 12.5}. With edits, nothing runs yet: a new '
            "preview and action_id come back for the user to approve."
        ),
    )


class CancelActionArgs(BaseModel):
    action_id: str = Field(..., description="The action_id to discard.")


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    arg_model: type[BaseModel] | None
    read_only: bool
    destructive: bool
    # Set for MCP-only tools; shared tools run through ``execute_tool``.
    handler: Handler | None = None
    # Extra MCP-only input check; returns an error message to refuse the call.
    precheck: Precheck | None = None
    # The shared registry entry, for tools the chat offers too.
    spec: ToolSpec | None = None

    @property
    def enabled(self) -> bool:
        """Hidden from clients (and refused) while its feature is off."""
        return self.spec is None or self.spec.enabled


def _bulk_size(args: dict[str, Any]) -> str | None:
    total = sum(
        len(items)
        for key in ("incomes", "expenses")
        if isinstance(items := args.get(key), list)
    )
    if total > settings.MCP_BULK_MAX_ITEMS:
        return (
            f"At most {settings.MCP_BULK_MAX_ITEMS} items per bulk call; "
            "split the request."
        )
    if total == 0:
        return "Nothing to do: pass at least one income or expense."
    return None


_error = err


def _validation_error(exc: ValidationError) -> Result:
    return _error(f"Invalid arguments: {describe_validation_error(exc)}")


TIME_ZONE_TOOLS = frozenset(
    name for name, spec in TOOL_SPECS.items() if spec.uses_time_zone
)


@cache
def _known_time_zones() -> frozenset[str]:
    return frozenset(available_timezones())


def _today(time_zone: str | None) -> date:
    return local_today(time_zone or "UTC")


def _default_dates(
    name: str, args: dict[str, Any], time_zone: str | None = None
) -> dict[str, Any]:
    """Fill missing dates with the user's today (UTC when the client sends no
    ``time_zone``), as the in-app chat does with the browser's date."""
    spec = TOOL_SPECS.get(name)
    if spec is None:
        return args
    return apply_date_defaults(spec, args, _today(time_zone), time_zone)


async def _stage(
    name: str,
    args: dict[str, Any],
    db: AsyncSession,
    user: models.User,
    language: str,
) -> Result:
    """Validate a risky call, store it as pending and return its preview."""
    try:
        TOOL_ARG_MODELS[name](**args)
    except ValidationError as exc:
        return _validation_error(exc)
    try:
        staged = await stage_action(
            db,
            user,
            name,
            args,
            conversation_id=MCP_CONVERSATION_ID,
            response_language=language,
        )
    except (ServiceError, ToolExecutionError) as exc:
        return _error(exc.message)
    return {
        "success": True,
        "status": "confirmation_required",
        "action_id": staged.action_id,
        "expires_in_seconds": CHAT_PENDING_TTL,
        "preview": jsonable_encoder(staged.preview),
        "message": staged.message,
        "next_step": message(language, "next_step"),
    }


async def _confirm(
    arguments: dict[str, Any], db: AsyncSession, user: models.User, language: str
) -> Result:
    try:
        parsed = ConfirmActionArgs(**arguments)
    except ValidationError as exc:
        return _validation_error(exc)
    try:
        pending = await claim_current_action(
            db, user, parsed.action_id, MCP_CONVERSATION_ID
        )
    except (PendingActionNotFound, PendingActionConversationMismatch):
        # An in-app chat action looks the same as a missing one from here.
        return _error(message(language, "not_found"))
    except PreviewOutdated:
        return _error(message(language, "outdated"))
    tool_name = pending["tool_name"]
    try:
        tool_args, edited = apply_untrusted_confirm_edits(
            tool_name, pending["tool_args"], parsed.edits
        )
    except ConfirmEditRejected as exc:
        return _error(exc.message)
    staged_language = tool_args.setdefault("response_language", language)
    if edited:
        # The user approved the old preview, not the edited one: stage the
        # edited call as a new action so the user sees what will run.
        return await _stage(
            tool_name,
            _default_dates(tool_name, tool_args),
            db,
            user,
            staged_language if staged_language in LANGUAGES else language,
        )
    result, _ = await execute_tool(
        tool_name, tool_args, db, user, made_from=MADE_FROM_MCP
    )
    return result


async def _cancel(
    arguments: dict[str, Any], _db: AsyncSession, user: models.User, language: str
) -> Result:
    try:
        parsed = CancelActionArgs(**arguments)
    except ValidationError as exc:
        return _validation_error(exc)
    try:
        await discard_action(parsed.action_id, user.id, MCP_CONVERSATION_ID)
    except (PendingActionNotFound, PendingActionConversationMismatch):
        return _error(message(language, "not_found"))
    return ok({"cancelled": True})


# MCP-only input checks on shared tools.
_PRECHECKS: dict[str, Precheck] = {
    "bulk_create_transactions": _bulk_size,
    "bulk_delete_transactions": _bulk_size,
}


def _shared(spec: ToolSpec) -> McpTool:
    return McpTool(
        name=spec.name,
        description=spec.description,
        arg_model=TOOL_ARG_MODELS.get(spec.name),
        read_only=spec.read_only,
        destructive=spec.destructive,
        precheck=_PRECHECKS.get(spec.name),
        spec=spec,
    )


_TOOL_LIST: tuple[McpTool, ...] = (
    *(_shared(spec) for spec in TOOL_SPECS.values()),
    McpTool(
        name=CONFIRM_TOOL,
        description=(
            "Execute a pending action after the user has explicitly approved "
            "the preview. Pass the action_id from a confirmation_required "
            "result. If the user asked for changes, pass them as edits: nothing "
            "runs yet and a new preview and action_id come back for the user to "
            "approve."
        ),
        arg_model=ConfirmActionArgs,
        read_only=False,
        destructive=True,
        handler=_confirm,
    ),
    McpTool(
        name=CANCEL_TOOL,
        description=(
            "Discard a pending action the user did not approve. Pass the "
            "action_id from a confirmation_required result."
        ),
        arg_model=CancelActionArgs,
        read_only=False,
        destructive=False,
        handler=_cancel,
    ),
)

MCP_TOOLS: dict[str, McpTool] = {tool.name: tool for tool in _TOOL_LIST}


def enabled_tools() -> list[McpTool]:
    """The tools to advertise now: those whose feature is switched on."""
    return [tool for tool in MCP_TOOLS.values() if tool.enabled]


def _inline_refs(node: Any, defs: dict[str, Any], stack: tuple[str, ...] = ()) -> Any:
    """Replace every ``$ref`` with the definition it points at."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name in stack:
                raise RuntimeError(f"Recursive schema reference: {name}")
            resolved = _inline_refs(defs[name], defs, (*stack, name))
            extras = {
                k: _inline_refs(v, defs, stack) for k, v in node.items() if k != "$ref"
            }
            return {**resolved, **extras}
        return {k: _inline_refs(v, defs, stack) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs, stack) for item in node]
    return node


def input_schema(tool: McpTool) -> dict[str, Any]:
    """JSON Schema for a tool's input, self-contained and free of chat-only fields."""
    if tool.arg_model is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    raw = tool.arg_model.model_json_schema()
    defs = raw.pop("$defs", {})
    schema = _inline_refs(raw, defs)
    properties = schema.get("properties", {})
    for field in _CHAT_ONLY_FIELDS:
        properties.pop(field, None)
    required = [f for f in schema.get("required", []) if f not in _CHAT_ONLY_FIELDS]
    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)
    schema.pop("title", None)
    if tool.name in TIME_ZONE_TOOLS:
        schema.setdefault("properties", {})[TIME_ZONE_FIELD] = dict(_TIME_ZONE_SCHEMA)
    if _takes_language(tool):
        schema.setdefault("properties", {})[LANGUAGE_FIELD] = dict(_LANGUAGE_SCHEMA)
    return schema


def _takes_language(tool: McpTool) -> bool:
    """Tools whose result includes Toston's own wording: approvals and their steps."""
    if tool.handler is not None:
        return True
    return (
        tool.arg_model is not None
        and "response_language" in tool.arg_model.model_fields
    )


async def call_tool(
    name: str,
    arguments: dict[str, Any] | None,
    db: AsyncSession,
    user: models.User,
) -> Result:
    """Run one MCP tool call and return a plain dict (never raises for bad input)."""
    tool = MCP_TOOLS.get(name)
    if tool is None or not tool.enabled:
        return _error(f"Unknown tool: {name}")

    args = {k: v for k, v in (arguments or {}).items() if k not in _CHAT_ONLY_FIELDS}
    language = args.pop(LANGUAGE_FIELD, None) or language_of(user)
    if language not in LANGUAGES:
        return _error(message(language_of(user), "unknown_language", value=language))
    time_zone = args.pop(TIME_ZONE_FIELD, None)
    if time_zone is not None and (
        not isinstance(time_zone, str) or time_zone not in _known_time_zones()
    ):
        return _error(message(language, "unknown_time_zone", value=time_zone))
    if tool.handler is not None:
        return await tool.handler(args, db, user, language)

    model_fields = tool.arg_model.model_fields if tool.arg_model else {}
    if "response_language" in model_fields:
        args["response_language"] = language
    if tool.precheck is not None and (problem := tool.precheck(args)):
        return _error(problem)

    args = _default_dates(name, args, time_zone)
    if requires_confirmation(name, args):
        return await _stage(name, args, db, user, language)

    result, _ = await execute_tool(name, args, db, user, made_from=MADE_FROM_MCP)
    return result
