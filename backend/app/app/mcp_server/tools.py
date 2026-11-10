"""MCP-facing adapter over ``ai.chat_tools.execute_tool``; no transport.

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
from datetime import datetime, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.chat_tools import execute_tool
from app.ai.confirmation import (
    DESTRUCTIVE_TOOLS,
    MANAGE_DELETE_ACTIONS,
    ConfirmEditRejected,
    PendingActionConversationMismatch,
    PendingActionNotFound,
    ToolExecutionError,
    apply_untrusted_confirm_edits,
    claim_action,
    discard_action,
    requires_confirmation,
    stage_action,
)
from app.ai.tools import TOOL_ARG_MODELS
from app.core.config import settings
from app.schemas.made_from import MADE_FROM_MCP
from app.services.errors import ServiceError
from app.utilities.redis import CHAT_PENDING_TTL

MCP_CONVERSATION_ID = "mcp"
# MCP hosts answer in the user's language themselves; Toston's own messages
# (previews, confirmations) are always English.
MCP_LANGUAGE = "en"
CONFIRM_TOOL = "confirm_action"
CANCEL_TOOL = "cancel_action"
# Fields that only matter to Toston's own chat UI; MCP clients never see them.
_CHAT_ONLY_FIELDS = ("presentation", "response_language")

_NOT_FOUND_ERROR = "Action not found, expired, or already confirmed"

Result = dict[str, Any]
Handler = Callable[[dict[str, Any], AsyncSession, models.User], Awaitable[Result]]
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
    summary: str
    arg_model: type[BaseModel] | None
    read_only: bool
    # Set for MCP-only tools; dispatcher tools leave it None.
    handler: Handler | None = None
    # Extra MCP-only input check; returns an error message to refuse the call.
    precheck: Precheck | None = None

    @property
    def destructive(self) -> bool:
        """MCP ``destructiveHint``: the call may delete or overwrite data."""
        if self.read_only:
            return False
        if self.handler is not None:
            return self.name == CONFIRM_TOOL
        return (
            self.name in DESTRUCTIVE_TOOLS
            or self.name in MANAGE_DELETE_ACTIONS
            or self.name.startswith("update_")
        )

    @property
    def description(self) -> str:
        """``summary`` plus the confirmation rule, derived from the policy."""
        if self.read_only or self.handler is not None:
            return self.summary
        if self.name in MANAGE_DELETE_ACTIONS:
            rule = "Creates run immediately; updates and deletes require confirmation."
        elif requires_confirmation(self.name, {}):
            rule = "Requires confirmation."
        else:
            rule = "It runs immediately."
        return f"{self.summary} {rule}"


def _read(name: str, summary: str) -> McpTool:
    return McpTool(name, summary, TOOL_ARG_MODELS.get(name), read_only=True)


def _write(name: str, summary: str, *, precheck: Precheck | None = None) -> McpTool:
    return McpTool(
        name, summary, TOOL_ARG_MODELS[name], read_only=False, precheck=precheck
    )


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


def _error(message: str) -> Result:
    return {"success": False, "error": message}


def _validation_error(exc: ValidationError) -> Result:
    details = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
    )
    return _error(f"Invalid arguments: {details}")


_DATED_CREATE_TOOLS = ("create_expense", "create_income", "create_transfer")


def _default_dates(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Fill a missing ``date`` with today (UTC), as the in-app chat does.

    The chat agent fills dates from the user's local date before dispatch; MCP
    goes straight to ``execute_tool``, whose schemas leave a missing date NULL,
    which hides the record from every period summary. MCP clients send no time
    zone, so UTC is the best server-side default.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    if name in _DATED_CREATE_TOOLS:
        if not args.get("date"):
            args = {**args, "date": today}
    elif name == "bulk_create_transactions":
        args = dict(args)
        for key in ("incomes", "expenses"):
            items = args.get(key)
            if isinstance(items, list):
                args[key] = [
                    {**item, "date": today}
                    if isinstance(item, dict) and not item.get("date")
                    else item
                    for item in items
                ]
    return args


async def _stage(
    name: str, args: dict[str, Any], db: AsyncSession, user: models.User
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
            response_language=MCP_LANGUAGE,
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
        "next_step": (
            "Show this preview to the user. Call confirm_action with this "
            "action_id ONLY after the user explicitly approves; otherwise "
            "call cancel_action."
        ),
    }


async def _confirm(
    arguments: dict[str, Any], db: AsyncSession, user: models.User
) -> Result:
    try:
        parsed = ConfirmActionArgs(**arguments)
    except ValidationError as exc:
        return _validation_error(exc)
    try:
        pending = await claim_action(parsed.action_id, user.id, MCP_CONVERSATION_ID)
    except (PendingActionNotFound, PendingActionConversationMismatch):
        # An in-app chat action looks the same as a missing one from here.
        return _error(_NOT_FOUND_ERROR)
    tool_name = pending["tool_name"]
    try:
        tool_args, edited = apply_untrusted_confirm_edits(
            tool_name, pending["tool_args"], parsed.edits
        )
    except ConfirmEditRejected as exc:
        return _error(exc.message)
    tool_args.setdefault("response_language", MCP_LANGUAGE)
    if edited:
        # The user approved the old preview, not the edited one: stage the
        # edited call as a new action so the user sees what will run.
        return await _stage(tool_name, _default_dates(tool_name, tool_args), db, user)
    result, _ = await execute_tool(
        tool_name, tool_args, db, user, confirmed=True, made_from=MADE_FROM_MCP
    )
    return result


async def _cancel(
    arguments: dict[str, Any], _db: AsyncSession, user: models.User
) -> Result:
    try:
        parsed = CancelActionArgs(**arguments)
    except ValidationError as exc:
        return _validation_error(exc)
    try:
        await discard_action(parsed.action_id, user.id, MCP_CONVERSATION_ID)
    except (PendingActionNotFound, PendingActionConversationMismatch):
        return _error(_NOT_FOUND_ERROR)
    return {"success": True, "data": {"cancelled": True}}


_TOOL_LIST: tuple[McpTool, ...] = (
    _read(
        "get_user_profile",
        "Get the user's profile (name and country). Use it to personalize "
        "answers; it does not include balances or currency.",
    ),
    _read(
        "list_accounts",
        "List the user's accounts with their balances. Use it to find the "
        "account ids that other tools require.",
    ),
    _read(
        "list_categories",
        "List the user's categories and their subcategories. Use it to find "
        "the category and subcategory ids that other tools require.",
    ),
    _read(
        "list_places",
        "List the user's saved places (merchants or locations). Use it to find "
        "the place ids that other tools require.",
    ),
    _read(
        "search_transactions",
        "Search the user's expenses, incomes and transfers by date, amount, "
        "text, account, category or place. Returned transaction ids can be "
        "used with the update_* and delete_* tools.",
    ),
    _read(
        "get_period_summary",
        "Get totals for a period: income, expenses and balance, with "
        "breakdowns. Use it to answer how much was spent or earned over a "
        "date range.",
    ),
    _read(
        "get_period_charts",
        "Get chart-ready series for a period, such as spending over time and "
        "by category. Use it for trends and comparisons.",
    ),
    _read(
        "list_balance_adjustments",
        "List past balance adjustments, which are manual corrections to an "
        "account balance. Account ids come from list_accounts.",
    ),
    _write(
        "manage_account",
        "Create, update or delete an account (bank account, cash, card). "
        "Updates and deletes need an id from list_accounts.",
    ),
    _write(
        "manage_category",
        "Create, update or delete a category. Updates and deletes need an id "
        "from list_categories.",
    ),
    _write(
        "manage_subcategory",
        "Create, update or delete a subcategory under a category. Updates and "
        "deletes need ids from list_categories.",
    ),
    _write(
        "manage_place",
        "Create, update or delete a saved place. Updates and deletes need an "
        "id from list_places.",
    ),
    _write(
        "create_expense",
        "Record a new expense. Account, category and place ids come from the "
        "list_* tools.",
    ),
    _write(
        "update_expense",
        "Change fields of an existing expense. The id comes from search_transactions.",
    ),
    _write(
        "delete_expense",
        "Delete an expense. The id comes from search_transactions.",
    ),
    _write(
        "create_income",
        "Record a new income. Account and category ids come from the list_* tools.",
    ),
    _write(
        "update_income",
        "Change fields of an existing income. The id comes from search_transactions.",
    ),
    _write(
        "delete_income",
        "Delete an income. The id comes from search_transactions.",
    ),
    _write(
        "create_transfer",
        "Move money between two of the user's accounts. Account ids come from "
        "list_accounts.",
    ),
    _write(
        "update_transfer",
        "Change fields of an existing transfer. The id comes from search_transactions.",
    ),
    _write(
        "delete_transfer",
        "Delete a transfer. The id comes from search_transactions.",
    ),
    _write(
        "bulk_create_transactions",
        "Create many incomes and expenses in one call, for example when "
        "importing a list. The size is capped per call.",
        precheck=_bulk_size,
    ),
    _write(
        "bulk_delete_transactions",
        "Delete many incomes and expenses by id in one call. Ids come from "
        "search_transactions. The size is capped per call.",
        precheck=_bulk_size,
    ),
    _write(
        "create_balance_adjustment",
        "Set an account's balance to a stated value by recording an "
        "adjustment. The account id comes from list_accounts.",
    ),
    McpTool(
        CONFIRM_TOOL,
        "Execute a pending action after the user has explicitly approved the "
        "preview. Pass the action_id from a confirmation_required result. If "
        "the user asked for changes, pass them as edits: nothing runs yet and a "
        "new preview and action_id come back for the user to approve.",
        ConfirmActionArgs,
        read_only=False,
        handler=_confirm,
    ),
    McpTool(
        CANCEL_TOOL,
        "Discard a pending action the user did not approve. Pass the "
        "action_id from a confirmation_required result.",
        CancelActionArgs,
        read_only=False,
        handler=_cancel,
    ),
)

MCP_TOOLS: dict[str, McpTool] = {tool.name: tool for tool in _TOOL_LIST}


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
    return schema


async def call_tool(
    name: str,
    arguments: dict[str, Any] | None,
    db: AsyncSession,
    user: models.User,
) -> Result:
    """Run one MCP tool call and return a plain dict (never raises for bad input)."""
    tool = MCP_TOOLS.get(name)
    if tool is None:
        return _error(f"Unknown tool: {name}")

    args = {k: v for k, v in (arguments or {}).items() if k not in _CHAT_ONLY_FIELDS}
    if tool.handler is not None:
        return await tool.handler(args, db, user)

    model_fields = tool.arg_model.model_fields if tool.arg_model else {}
    if "response_language" in model_fields:
        args["response_language"] = MCP_LANGUAGE
    if tool.precheck is not None and (problem := tool.precheck(args)):
        return _error(problem)

    args = _default_dates(name, args)
    if requires_confirmation(name, args):
        return await _stage(name, args, db, user)

    result, _ = await execute_tool(name, args, db, user, made_from=MADE_FROM_MCP)
    return result
