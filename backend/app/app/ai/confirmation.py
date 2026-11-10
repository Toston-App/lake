"""Confirmation for risky chat and MCP tool calls: policy, previews, lifecycle.

Every caller (the in-app chat agent, the chat confirm endpoint and the MCP
server) uses this module, so the rules for what needs approval, what a preview
shows and how an approved action is claimed live in one place:

* Policy: ``requires_confirmation`` decides which calls are staged.
* Previews: ``build_pending_preview`` and ``format_confirmation_message``.
* Lifecycle: ``stage_action`` stores a pending action in Redis;
  ``claim_action`` atomically takes it back for execution, so an action runs at
  most once; ``discard_action`` drops it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.ai.tools import LIST_LIMIT
from app.schemas.made_from import MADE_FROM_CHAT
from app.services import accounts as account_service
from app.services import catalog as catalog_service
from app.utilities.redis import (
    claim_pending_action,
    delete_pending_action,
    get_pending_action,
    store_pending_action,
)

DESTRUCTIVE_TOOLS = frozenset(
    {
        "delete_expense",
        "delete_income",
        "delete_transfer",
        "bulk_delete_transactions",
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


def merge_confirm_tool_args(
    tool_name: str, stored_args: dict, override: dict | None
) -> dict:
    if not override:
        return stored_args
    if tool_name.startswith("update_"):
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


async def _owned_expense(db, user, expense_id: int):
    expense = await crud.expense.get(db, id=expense_id)
    if not expense or (
        expense.owner_id != user.id and not crud.user.is_superuser(user)
    ):
        raise ToolExecutionError("Expense not found")
    return expense


async def _owned_income(db, user, income_id: int):
    income = await crud.income.get(db, id=income_id)
    if not income or (income.owner_id != user.id and not crud.user.is_superuser(user)):
        raise ToolExecutionError("Income not found")
    return income


async def _owned_transfer(db, user, transfer_id: int):
    transfer = await crud.transfer.get(db, id=transfer_id)
    if not transfer or (
        transfer.owner_id != user.id and not crud.user.is_superuser(user)
    ):
        raise ToolExecutionError("Transfer not found")
    return transfer


async def _label_account(
    db: AsyncSession, user: models.User, account_id: int | None
) -> str | None:
    if account_id is None:
        return None
    account = await crud.account.get_by_id(db, owner_id=user.id, id=account_id)
    return account.name if account else f"Account #{account_id}"


def _owned_name(user: models.User, record: Any) -> str | None:
    """Name of ``record`` if it belongs to ``user`` (the services' ownership rule).

    Previews must not reveal the names of other users' records.
    """
    if record is None or (
        record.owner_id != user.id and not crud.user.is_superuser(user)
    ):
        return None
    return record.name


async def _label_category(
    db: AsyncSession, user: models.User, category_id: int | None
) -> str | None:
    if category_id is None:
        return None
    category = await crud.category.get(db=db, id=category_id)
    return _owned_name(user, category) or f"Category #{category_id}"


async def _label_subcategory(
    db: AsyncSession, user: models.User, subcategory_id: int | None
) -> str | None:
    if subcategory_id is None:
        return None
    sub = await crud.subcategory.get(db=db, id=subcategory_id)
    return _owned_name(user, sub) or f"Subcategory #{subcategory_id}"


async def _label_place(
    db: AsyncSession, user: models.User, place_id: int | None
) -> str | None:
    if place_id is None:
        return None
    place = await crud.place.get(db=db, id=place_id)
    return _owned_name(user, place) or f"Place #{place_id}"


async def _expense_field_labels(
    db: AsyncSession, user: models.User, fields: dict
) -> dict[str, str]:
    labels: dict[str, str] = {}
    if fields.get("account_id") is not None:
        name = await _label_account(db, user, fields["account_id"])
        if name:
            labels["account_id"] = name
    if fields.get("category_id") is not None:
        name = await _label_category(db, user, fields["category_id"])
        if name:
            labels["category_id"] = name
    if fields.get("subcategory_id") is not None:
        name = await _label_subcategory(db, user, fields["subcategory_id"])
        if name:
            labels["subcategory_id"] = name
    if fields.get("place_id") is not None:
        name = await _label_place(db, user, fields["place_id"])
        if name:
            labels["place_id"] = name
    return labels


async def _income_field_labels(
    db: AsyncSession, user: models.User, fields: dict
) -> dict[str, str]:
    labels: dict[str, str] = {}
    if fields.get("account_id") is not None:
        name = await _label_account(db, user, fields["account_id"])
        if name:
            labels["account_id"] = name
    if fields.get("subcategory_id") is not None:
        name = await _label_subcategory(db, user, fields["subcategory_id"])
        if name:
            labels["subcategory_id"] = name
    if fields.get("place_id") is not None:
        name = await _label_place(db, user, fields["place_id"])
        if name:
            labels["place_id"] = name
    return labels


async def _transfer_field_labels(
    db: AsyncSession, user: models.User, fields: dict
) -> dict[str, str]:
    labels: dict[str, str] = {}
    if fields.get("from_acc") is not None:
        name = await _label_account(db, user, fields["from_acc"])
        if name:
            labels["from_acc"] = name
    if fields.get("to_acc") is not None:
        name = await _label_account(db, user, fields["to_acc"])
        if name:
            labels["to_acc"] = name
    return labels


async def _transaction_field_choices(
    db: AsyncSession, user: models.User, transaction_type: str
) -> dict[str, list[dict[str, Any]]]:
    accounts = await account_service.list_accounts(db, user, skip=0, limit=LIST_LIMIT)
    account_choices = [{"value": item.id, "label": item.name} for item in accounts]
    if transaction_type == "transfer":
        return {"from_acc": account_choices, "to_acc": account_choices}

    categories = await catalog_service.list_categories(
        db, user, skip=0, limit=LIST_LIMIT
    )
    places = await catalog_service.list_places(db, user, skip=0, limit=LIST_LIMIT)
    is_income = transaction_type == "income"
    matching_categories = [c for c in categories if bool(c.is_income) is is_income]
    subcategories = [
        {
            "value": sub.id,
            "label": sub.name,
            "parent_value": sub.category_id,
        }
        for category in matching_categories
        for sub in (getattr(category, "subcategories", None) or [])
    ]
    choices: dict[str, list[dict[str, Any]]] = {
        "account_id": account_choices,
        "subcategory_id": subcategories,
        "place_id": [{"value": item.id, "label": item.name} for item in places],
    }
    if not is_income:
        choices["category_id"] = [
            {"value": item.id, "label": item.name} for item in matching_categories
        ]
    return choices


def _serialize_record(record: Any) -> dict:
    data = jsonable_encoder(record)
    if isinstance(data.get("date"), (date, datetime)):
        data["date"] = data["date"].isoformat()[:10]
    elif data.get("date"):
        data["date"] = str(data["date"])[:10]
    return data


def _without_internal_args(tool_args: dict) -> dict:
    return {
        key: value for key, value in tool_args.items() if key != "response_language"
    }


async def build_pending_preview(
    db: AsyncSession,
    user: models.User,
    tool_name: str,
    tool_args: dict,
) -> dict:
    """Structured preview for the chat UI (read-only deletes, editable creates/updates)."""
    tool_args = _without_internal_args(tool_args)
    if tool_name == "create_expense":
        fields = dict(tool_args)
        fields.setdefault("made_from", MADE_FROM_CHAT)
        return {
            "kind": tool_name,
            "operation": "create",
            "transaction_type": "expense",
            "fields": fields,
            "labels": await _expense_field_labels(db, user, fields),
        }

    if tool_name == "update_expense":
        expense = await _owned_expense(db, user, tool_args["id"])
        existing = _serialize_record(expense)
        patch = dict(tool_args.get("data") or {})
        fields = {**existing, **patch}
        # we can remove choices, labels, patch and existing. these are only used for UI, but we can get it from state.
        return {
            "kind": tool_name,
            "operation": "update",
            "transaction_type": "expense",
            "record_id": tool_args["id"],
            "fields": fields,
            "patch": patch,
            "existing": existing,
            "labels": await _expense_field_labels(db, user, fields),
            "choices": await _transaction_field_choices(db, user, "expense"),
        }

    if tool_name == "delete_expense":
        expense = await _owned_expense(db, user, tool_args["id"])
        fields = _serialize_record(expense)
        return {
            "kind": tool_name,
            "operation": "delete",
            "transaction_type": "expense",
            "record_id": tool_args["id"],
            "fields": fields,
            "labels": await _expense_field_labels(db, user, fields),
        }

    if tool_name == "create_income":
        fields = dict(tool_args)
        fields.setdefault("made_from", MADE_FROM_CHAT)
        return {
            "kind": tool_name,
            "operation": "create",
            "transaction_type": "income",
            "fields": fields,
            "labels": await _income_field_labels(db, user, fields),
        }

    if tool_name == "update_income":
        income = await _owned_income(db, user, tool_args["id"])
        existing = _serialize_record(income)
        patch = dict(tool_args.get("data") or {})
        fields = {**existing, **patch}
        return {
            "kind": tool_name,
            "operation": "update",
            "transaction_type": "income",
            "record_id": tool_args["id"],
            "fields": fields,
            "patch": patch,
            "existing": existing,
            "labels": await _income_field_labels(db, user, fields),
            "choices": await _transaction_field_choices(db, user, "income"),
        }

    if tool_name == "delete_income":
        income = await _owned_income(db, user, tool_args["id"])
        fields = _serialize_record(income)
        return {
            "kind": tool_name,
            "operation": "delete",
            "transaction_type": "income",
            "record_id": tool_args["id"],
            "fields": fields,
            "labels": await _income_field_labels(db, user, fields),
        }

    if tool_name == "create_transfer":
        fields = dict(tool_args)
        return {
            "kind": tool_name,
            "operation": "create",
            "transaction_type": "transfer",
            "fields": fields,
            "labels": await _transfer_field_labels(db, user, fields),
            "choices": await _transaction_field_choices(db, user, "transfer"),
        }

    if tool_name == "update_transfer":
        transfer = await _owned_transfer(db, user, tool_args["id"])
        existing = _serialize_record(transfer)
        patch = dict(tool_args.get("data") or {})
        fields = {**existing, **patch}
        return {
            "kind": tool_name,
            "operation": "update",
            "transaction_type": "transfer",
            "record_id": tool_args["id"],
            "fields": fields,
            "patch": patch,
            "existing": existing,
            "labels": await _transfer_field_labels(db, user, fields),
            "choices": await _transaction_field_choices(db, user, "transfer"),
        }

    if tool_name == "delete_transfer":
        transfer = await _owned_transfer(db, user, tool_args["id"])
        fields = _serialize_record(transfer)
        return {
            "kind": tool_name,
            "operation": "delete",
            "transaction_type": "transfer",
            "record_id": tool_args["id"],
            "fields": fields,
            "labels": await _transfer_field_labels(db, user, fields),
        }

    if tool_name.startswith("manage_"):
        entity_type = tool_name.removeprefix("manage_")
        operation = str(tool_args.get("action") or "update")
        display_name = None
        record_id = tool_args.get("id")
        if record_id is not None:
            if entity_type == "account":
                record = await account_service.get_account(db, user, record_id)
            elif entity_type == "category":
                record = await catalog_service.get_category(db, user, record_id)
            elif entity_type == "subcategory":
                record = await catalog_service.get_subcategory(db, user, record_id)
            else:
                record = await catalog_service.get_place(db, user, record_id)
            display_name = getattr(record, "name", None)
        return {
            "kind": tool_name,
            "operation": operation,
            "entity_type": entity_type,
            "display_name": display_name,
            "changes": tool_args.get("data") or {},
        }

    if tool_name == "create_balance_adjustment":
        fields = dict(tool_args)
        return {
            "kind": tool_name,
            "operation": "create",
            "entity_type": "balance_adjustment",
            "fields": fields,
            "labels": {
                "account_id": await _label_account(db, user, fields.get("account_id"))
            },
        }

    if tool_name == "bulk_create_transactions":
        items: list[dict[str, Any]] = []
        for tx_type, rows in (
            ("income", tool_args.get("incomes") or []),
            ("expense", tool_args.get("expenses") or []),
        ):
            for row in rows:
                fields = dict(row)
                labeler = (
                    _income_field_labels
                    if tx_type == "income"
                    else _expense_field_labels
                )
                items.append(
                    {
                        "transaction_type": tx_type,
                        "fields": fields,
                        "labels": await labeler(db, user, fields),
                    }
                )
        return {
            "kind": tool_name,
            "operation": "create",
            "entity_type": "transactions",
            "items": items,
        }

    if tool_name == "bulk_delete_transactions":
        items = []
        for tx_id in tool_args.get("incomes") or []:
            record = await _owned_income(db, user, tx_id)
            items.append(
                {"transaction_type": "income", "fields": _serialize_record(record)}
            )
        for tx_id in tool_args.get("expenses") or []:
            record = await _owned_expense(db, user, tx_id)
            items.append(
                {"transaction_type": "expense", "fields": _serialize_record(record)}
            )
        return {
            "kind": tool_name,
            "operation": "delete",
            "entity_type": "transactions",
            "items": items,
        }

    return {
        "kind": tool_name,
        "operation": "delete" if is_destructive_tool(tool_name, tool_args) else "other",
        "arguments": tool_args,
    }


def format_confirmation_message(
    tool_name: str, preview: dict, response_language: str = "en"
) -> str:
    _ = tool_name
    language = "es" if response_language == "es" else "en"
    operation = preview.get("operation", "other")
    tx_type = preview.get("transaction_type", "action")
    fields = preview.get("fields") or {}
    labels = preview.get("labels") or {}
    type_names = {
        "en": {"expense": "expense", "income": "income", "transfer": "transfer"},
        "es": {"expense": "gasto", "income": "ingreso", "transfer": "transferencia"},
    }
    visible_type = type_names[language].get(
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
