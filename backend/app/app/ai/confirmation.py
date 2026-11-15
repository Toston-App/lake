"""Confirmation for risky chat and MCP tool calls: policy, previews, lifecycle.

Every caller (the in-app chat agent, the chat confirm endpoint and the MCP
server) uses this module, so the rules for what needs approval, what a preview
shows and how an approved action is claimed live in one place:

* Policy: ``requires_confirmation`` decides which calls are staged.
* Previews: ``build_pending_preview`` (``ai.previews``) and
  ``format_confirmation_message``.
* Lifecycle: ``stage_action`` stores a pending action in Redis;
  ``claim_action`` atomically takes it back for execution, so an action runs at
  most once; ``discard_action`` drops it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.export_tools import ENTITY_TYPE as EXPORT_ENTITY_TYPE
from app.ai.export_tools import format_export_message
from app.ai.investment_tools import ENTITY_TYPE as INVESTMENT_ENTITY_TYPE
from app.ai.investment_tools import format_investment_message
from app.ai.previews import (  # noqa: F401 - build_pending_preview is re-exported
    CONVERSION_ENTITY_TYPE,
    build_pending_preview,
)
from app.ai.recurring_tools import ENTITY_TYPE as RECURRING_ENTITY_TYPE
from app.ai.recurring_tools import format_recurring_message
from app.ai.tools import TOOL_ARG_MODELS
from app.utilities.redis import (
    claim_pending_action,
    delete_pending_action,
    get_pending_action,
    store_pending_action,
)

# The conversation id MCP stages its pending actions under; reserved, so no
# in-app chat conversation can use it.
MCP_CONVERSATION_ID = "mcp"

DESTRUCTIVE_TOOLS = frozenset(
    {
        "delete_expense",
        "delete_income",
        "delete_transfer",
        "bulk_delete_transactions",
        "delete_recurring",
        # Deletes the original and creates its replacement.
        "convert_transaction",
        # Replaces (deletes) the user's previous export file.
        "export_data",
    }
)

MANAGE_DELETE_ACTIONS = frozenset(
    {"manage_account", "manage_category", "manage_subcategory", "manage_place"}
)

RISKY_CONFIRMATION_TOOLS = frozenset(
    {
        "create_transfer",
        "update_expense",
        "update_income",
        "update_transfer",
        "create_balance_adjustment",
        "bulk_create_transactions",
        "create_recurring",
        "create_recurring_from_transaction",
        "update_recurring",
        "skip_recurring_occurrence",
        "update_profile",
        # Uses the free plan's only scan, and with use_ai sends descriptions
        # to the AI provider: the user decides.
        "detect_recurring",
        "record_investment_transaction",
    }
)


class ToolExecutionError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def is_destructive_tool(tool_name: str, args: dict) -> bool:
    if tool_name in DESTRUCTIVE_TOOLS:
        return True
    if tool_name in MANAGE_DELETE_ACTIONS and args.get("action") == "delete":
        return True
    return False


def requires_confirmation(tool_name: str, args: dict) -> bool:
    if is_destructive_tool(tool_name, args):
        return True
    if tool_name in MANAGE_DELETE_ACTIONS and args.get("action") == "update":
        return True
    return tool_name in RISKY_CONFIRMATION_TOOLS


def _patches_data(tool_name: str) -> bool:
    """Tools that take ``{id, data}``: edits go into ``data``."""
    model = TOOL_ARG_MODELS.get(tool_name)
    return model is not None and {"id", "data"} <= model.model_fields.keys()


def merge_confirm_tool_args(
    tool_name: str, stored_args: dict, override: dict | None
) -> dict:
    if not override:
        return stored_args
    if _patches_data(tool_name):
        record_id = override.get("id", stored_args.get("id"))
        if isinstance(override.get("data"), dict):
            merged_data = {**stored_args.get("data", {}), **override["data"]}
        else:
            patch = {k: v for k, v in override.items() if k != "id"}
            merged_data = {**stored_args.get("data", {}), **patch}
        return {
            "id": record_id,
            "data": merged_data,
            "response_language": stored_args.get("response_language", "en"),
        }
    return {**stored_args, **override}


def format_confirmation_message(
    tool_name: str, preview: dict, response_language: str = "en"
) -> str:
    _ = tool_name
    language = "es" if response_language == "es" else "en"
    formatter = _ENTITY_MESSAGES.get(preview.get("entity_type"))
    if formatter is not None:
        return formatter(preview, language)
    operation = preview.get("operation", "other")
    tx_type = preview.get("transaction_type", "action")
    fields = preview.get("fields") or {}
    labels = preview.get("labels") or {}
    visible_type = type_names_for(language).get(
        tx_type, "acción" if language == "es" else "action"
    )

    if preview.get("items") is not None:
        count = len(preview.get("items") or [])
        if operation == "delete":
            return (
                f"Confirma que deseas eliminar **{count} transacciones**. Esta acción no se puede deshacer."
                if language == "es"
                else f"Confirm deletion of **{count} transactions**. This cannot be undone."
            )
        return (
            f"Revisa **{count} transacciones** y aprueba para guardarlas."
            if language == "es"
            else f"Review **{count} transactions**, then approve to save them."
        )

    if preview.get("entity_type") == "balance_adjustment":
        return (
            "Revisa el nuevo saldo y aprueba para aplicarlo."
            if language == "es"
            else "Review the new balance, then approve to apply it."
        )

    if preview.get("entity_type") and preview.get("display_name"):
        name = preview["display_name"]
        if operation == "delete":
            return (
                f"Confirma que deseas eliminar **{name}**. Esta acción no se puede deshacer."
                if language == "es"
                else f"Confirm deletion of **{name}**. This cannot be undone."
            )
        return (
            f"Revisa los cambios de **{name}** y aprueba para aplicarlos."
            if language == "es"
            else f"Review the changes to **{name}**, then approve to apply them."
        )

    if operation == "delete":
        amount = fields.get("amount")
        desc = fields.get("description") or ""
        amt_str = f" **{amount}**" if amount is not None else ""
        suffix = f": {desc}" if desc else ""
        if language == "es":
            return f"Confirma que deseas **eliminar** este {visible_type}{amt_str}{suffix}. Esta acción no se puede deshacer."
        return f"Please confirm you want to **delete** this {visible_type}{amt_str}{suffix}. This cannot be undone."

    if operation == "create":
        amount = fields.get("amount", "?")
        desc = fields.get("description") or ""
        account = labels.get("account_id") or labels.get("from_acc")
        suffix = f": {desc}" if desc else ""
        if language == "es":
            extra = f" en **{account}**" if account else ""
            return f"Revisa este nuevo **{visible_type}** de **{amount}**{extra}{suffix} y aprueba para guardar."
        extra = f" to **{account}**" if account else ""
        return f"Review this new **{visible_type}** of **{amount}**{extra}{suffix}, then approve to save."

    if operation == "update":
        if language == "es":
            return f"Revisa los cambios de este **{visible_type}** y aprueba para aplicarlos."
        return f"Review the changes to this **{visible_type}**, then approve to apply them."

    if language == "es":
        return "Revisa los detalles y confirma para continuar."
    return "Review the details and confirm to continue."


def type_names_for(language: str) -> dict[str, str]:
    if language == "es":
        return {"expense": "gasto", "income": "ingreso", "transfer": "transferencia"}
    return {"expense": "expense", "income": "income", "transfer": "transfer"}


def _conversion_message(preview: dict, language: str) -> str:
    names = type_names_for(language)
    before = names.get(preview["from_type"], preview["from_type"])
    after = names.get(preview["to_type"], preview["to_type"])
    amount = (preview.get("fields") or {}).get("amount", "?")
    if language == "es":
        return (
            f"Confirma que deseas convertir este {before} de **{amount}** en "
            f"{after}. El {before} se elimina y se reemplaza."
        )
    article = "an" if after[0] in "aeiou" else "a"
    return (
        f"Confirm turning this {before} of **{amount}** into {article} {after}. "
        f"The {before} is deleted and replaced."
    )


# Previews whose entity has its own wording, by ``entity_type``.
_ENTITY_MESSAGES: dict[str, Callable[[dict, str], str]] = {
    RECURRING_ENTITY_TYPE: format_recurring_message,
    CONVERSION_ENTITY_TYPE: _conversion_message,
    INVESTMENT_ENTITY_TYPE: format_investment_message,
    EXPORT_ENTITY_TYPE: format_export_message,
}


def format_confirmation_result(
    *,
    success: bool,
    response_language: str = "en",
    error: str | None = None,
) -> str:
    language = "es" if response_language == "es" else "en"
    if success:
        return (
            "Listo. La acción se completó correctamente."
            if language == "es"
            else "Done. The action completed successfully."
        )
    if error:
        prefix = (
            "No se pudo completar la acción"
            if language == "es"
            else "The action could not be completed"
        )
        return f"{prefix}: {error}"
    return (
        "No se pudo completar la acción."
        if language == "es"
        else "The action could not be completed."
    )


# -- lifecycle -----------------------------------------------------------------


@dataclass(frozen=True)
class StagedAction:
    action_id: str
    preview: dict
    message: str


class PendingActionNotFound(Exception):
    """No pending action with this id for this user (or it already ran)."""


class PendingActionConversationMismatch(Exception):
    """The pending action belongs to another conversation."""


class ConfirmEditRejected(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def stage_action(
    db: AsyncSession,
    user: models.User,
    tool_name: str,
    tool_args: dict,
    *,
    conversation_id: str,
    response_language: str,
    turn_id: int | None = None,
) -> StagedAction:
    """Build the preview and store the call as pending; nothing executes."""
    preview = await build_pending_preview(db, user, tool_name, tool_args)
    message = format_confirmation_message(tool_name, preview, response_language)
    action_id = str(uuid.uuid4())
    await store_pending_action(
        action_id=action_id,
        user_id=user.id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        tool_args=tool_args,
        preview=preview,
        response_language=response_language,
        turn_id=turn_id,
    )
    return StagedAction(action_id, preview, message)


async def _peek(action_id: str, user_id: int, conversation_id: str) -> dict:
    pending = await get_pending_action(action_id, user_id)
    if not pending:
        raise PendingActionNotFound()
    if pending.get("conversation_id") != conversation_id:
        raise PendingActionConversationMismatch()
    return pending


async def claim_action(action_id: str, user_id: int, conversation_id: str) -> dict:
    """Take a pending action for execution; only one caller ever gets it.

    The conversation is checked before the claim, so a confirm from one
    conversation (or from MCP) never consumes another conversation's action.
    """
    await _peek(action_id, user_id, conversation_id)
    pending = await claim_pending_action(action_id, user_id)
    if not pending:
        raise PendingActionNotFound()
    return pending


async def preview_is_current(
    db: AsyncSession, user: models.User, pending: dict
) -> bool:
    """Whether the record a pending action's preview showed is unchanged.

    Only previews of an existing record carry a ``record_version``. When it
    no longer matches, what would run differs from what the user approved.
    """
    expected = (pending.get("preview") or {}).get("record_version")
    if expected is None:
        return True
    try:
        current = await build_pending_preview(
            db, user, pending["tool_name"], pending["tool_args"]
        )
    except Exception:  # noqa: BLE001 - the record is gone or no longer valid
        return False
    return current.get("record_version") == expected


PREVIEW_OUTDATED = (
    "The record changed after this preview was shown. Ask again to see the "
    "current version."
)


class PreviewOutdated(Exception):
    """The record changed after the preview; the action was discarded."""

    def __init__(self, pending: dict) -> None:
        super().__init__(PREVIEW_OUTDATED)
        self.pending = pending


async def claim_current_action(
    db: AsyncSession,
    user: models.User,
    action_id: str,
    conversation_id: str,
) -> dict:
    """``claim_action``, after checking the preview still matches the record.

    An outdated action is discarded, not claimed, and raises PreviewOutdated:
    approving it again could never run what the user saw.
    """
    pending = await _peek(action_id, user.id, conversation_id)
    if not await preview_is_current(db, user, pending):
        await delete_pending_action(action_id)
        raise PreviewOutdated(pending)
    return await claim_action(action_id, user.id, conversation_id)


async def discard_action(action_id: str, user_id: int, conversation_id: str) -> None:
    await _peek(action_id, user_id, conversation_id)
    await delete_pending_action(action_id)


def apply_untrusted_confirm_edits(
    tool_name: str, stored_args: dict, edits: dict | None
) -> tuple[dict, bool]:
    """Merge edits that came from an LLM rather than from the user's own UI.

    Returns ``(merged_args, edited)``. Edits never retarget a record (``id``),
    never change a manage action (e.g. update into delete), and are refused
    outright for deletes and bulk creates, whose previews list exact targets.
    """
    edits = {k: v for k, v in (edits or {}).items() if k != "id"}
    if "action" in edits:
        if edits.pop("action") != stored_args.get("action"):
            raise ConfirmEditRejected("The action cannot be changed when confirming.")
    if not edits:
        return dict(stored_args), False
    if (
        is_destructive_tool(tool_name, stored_args)
        or tool_name == "bulk_create_transactions"
    ):
        raise ConfirmEditRejected(
            "This action can't be edited; cancel it and ask again."
        )
    return dict(merge_confirm_tool_args(tool_name, stored_args, edits)), True
