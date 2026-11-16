"""What a staged tool call will do, shown to the user before they approve it.

``build_pending_preview`` looks the tool up in ``PREVIEW_BUILDERS``. A preview
that shows an existing record (``existing``) also carries ``record_version``,
a digest of that record, so a confirm can tell when the record changed after
the user saw it (see ``confirmation.preview_is_current``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.ai.export_tools import EXPORT_PREVIEW_BUILDERS
from app.ai.investment_tools import INVESTMENT_PREVIEW_BUILDERS
from app.ai.preview_labels import (
    EXPENSE_FIELDS,
    INCOME_FIELDS,
    LABELERS,
    TRANSFER_FIELDS,
    label_account,
    labels_for,
)
from app.ai.recurring_tools import RECURRING_PREVIEW_BUILDERS
from app.ai.tools import LIST_LIMIT
from app.schemas.transaction import TransactionType
from app.services import accounts as account_service
from app.services import catalog as catalog_service
from app.services import profile as profile_service
from app.services import transaction_conversion as conversion_service
from app.services.errors import NotFoundError

PreviewBuilder = Callable[[AsyncSession, models.User, str, dict], Awaitable[dict]]

CONVERSION_ENTITY_TYPE = "transaction_conversion"


async def _owned(db: AsyncSession, user: models.User, kind: str, record_id: int):
    """The user's expense, income or transfer; another user's is reported missing."""
    record = await getattr(crud, kind).get(db, id=record_id)
    if not record or (record.owner_id != user.id and not crud.user.is_superuser(user)):
        raise NotFoundError(f"{kind.capitalize()} not found")
    return record


async def transaction_field_choices(
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


def serialize_record(record: Any) -> dict:
    data = jsonable_encoder(record)
    if isinstance(data.get("date"), (date, datetime)):
        data["date"] = data["date"].isoformat()[:10]
    elif data.get("date"):
        data["date"] = str(data["date"])[:10]
    return data


def record_version(existing: dict) -> str:
    payload = json.dumps(existing, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# -- expenses, incomes, transfers -----------------------------------------------

_LABEL_FIELDS = {
    "expense": EXPENSE_FIELDS,
    "income": INCOME_FIELDS,
    "transfer": TRANSFER_FIELDS,
}


def _create(kind: str) -> PreviewBuilder:
    async def build(db, user, tool_name, tool_args):
        # No made_from: the caller (chat or MCP) sets it when the call runs.
        fields = dict(tool_args)
        preview = {
            "kind": tool_name,
            "operation": "create",
            "transaction_type": kind,
            "fields": fields,
            "labels": await labels_for(db, user, fields, _LABEL_FIELDS[kind]),
        }
        if kind == "transfer":
            preview["choices"] = await transaction_field_choices(db, user, kind)
        return preview

    return build


def _update(kind: str) -> PreviewBuilder:
    async def build(db, user, tool_name, tool_args):
        existing = serialize_record(await _owned(db, user, kind, tool_args["id"]))
        patch = dict(tool_args.get("data") or {})
        fields = {**existing, **patch}
        return {
            "kind": tool_name,
            "operation": "update",
            "transaction_type": kind,
            "record_id": tool_args["id"],
            "fields": fields,
            "patch": patch,
            "existing": existing,
            "labels": await labels_for(db, user, fields, _LABEL_FIELDS[kind]),
            "choices": await transaction_field_choices(db, user, kind),
        }

    return build


def _delete(kind: str) -> PreviewBuilder:
    async def build(db, user, tool_name, tool_args):
        fields = serialize_record(await _owned(db, user, kind, tool_args["id"]))
        return {
            "kind": tool_name,
            "operation": "delete",
            "transaction_type": kind,
            "record_id": tool_args["id"],
            "fields": fields,
            "labels": await labels_for(db, user, fields, _LABEL_FIELDS[kind]),
        }

    return build


async def _bulk_create(db, user, tool_name, tool_args) -> dict:
    items: list[dict[str, Any]] = []
    for kind, rows in (
        ("income", tool_args.get("incomes") or []),
        ("expense", tool_args.get("expenses") or []),
    ):
        for row in rows:
            fields = dict(row)
            items.append(
                {
                    "transaction_type": kind,
                    "fields": fields,
                    "labels": await labels_for(db, user, fields, _LABEL_FIELDS[kind]),
                }
            )
    return {
        "kind": tool_name,
        "operation": "create",
        "entity_type": "transactions",
        "items": items,
    }


async def _bulk_delete(db, user, tool_name, tool_args) -> dict:
    items = []
    for kind, key in (("income", "incomes"), ("expense", "expenses")):
        for tx_id in tool_args.get(key) or []:
            record = await _owned(db, user, kind, tx_id)
            items.append({"transaction_type": kind, "fields": serialize_record(record)})
    return {
        "kind": tool_name,
        "operation": "delete",
        "entity_type": "transactions",
        "items": items,
    }


# -- catalog, balances, profile, conversion -----------------------------------------

_MANAGED_RECORDS = {
    "account": account_service.get_account,
    "category": catalog_service.get_category,
    "subcategory": catalog_service.get_subcategory,
    "place": catalog_service.get_place,
}


async def _manage(db, user, tool_name, tool_args) -> dict:
    entity_type = tool_name.removeprefix("manage_")
    display_name = None
    record_id = tool_args.get("id")
    if record_id is not None:
        record = await _MANAGED_RECORDS[entity_type](db, user, record_id)
        display_name = getattr(record, "name", None)
    return {
        "kind": tool_name,
        "operation": str(tool_args.get("action") or "update"),
        "entity_type": entity_type,
        "display_name": display_name,
        "changes": tool_args.get("data") or {},
    }


async def _balance_adjustment(db, user, tool_name, tool_args) -> dict:
    fields = dict(tool_args)
    return {
        "kind": tool_name,
        "operation": "create",
        "entity_type": "balance_adjustment",
        "fields": fields,
        "labels": {
            "account_id": await label_account(db, user, fields.get("account_id"))
        },
    }


async def _update_profile(db, user, tool_name, tool_args) -> dict:
    changes = {k: v for k, v in tool_args.items() if v is not None}
    if changes.get("default_account_id") is not None:
        changes["default_account"] = await label_account(
            db, user, changes["default_account_id"]
        )
    return {
        "kind": tool_name,
        "operation": "update",
        "entity_type": "profile",
        "display_name": "your profile",
        "existing": profile_service.profile_of(user),
        "changes": changes,
    }


async def _convert(db, user, tool_name, tool_args) -> dict:
    from_type = TransactionType(tool_args["from_type"])
    to_type = TransactionType(tool_args["to_type"])
    source = await conversion_service.load_source(db, user, from_type, tool_args["id"])
    # Raises now, before anything is staged, if the conversion can't work.
    fields = conversion_service.plan_conversion(
        source,
        from_type,
        to_type,
        conversion_service.ConversionTargets.from_args(tool_args),
    )
    return {
        "kind": tool_name,
        "operation": "convert",
        "labels": await labels_for(db, user, fields, LABELERS),
        "entity_type": CONVERSION_ENTITY_TYPE,
        "record_id": tool_args["id"],
        "from_type": from_type.value,
        "to_type": to_type.value,
        "existing": serialize_record(source),
        "fields": fields,
    }


PREVIEW_BUILDERS: dict[str, PreviewBuilder] = {
    **{f"create_{kind}": _create(kind) for kind in _LABEL_FIELDS},
    **{f"update_{kind}": _update(kind) for kind in _LABEL_FIELDS},
    **{f"delete_{kind}": _delete(kind) for kind in _LABEL_FIELDS},
    **{f"manage_{entity}": _manage for entity in _MANAGED_RECORDS},
    "bulk_create_transactions": _bulk_create,
    "bulk_delete_transactions": _bulk_delete,
    "create_balance_adjustment": _balance_adjustment,
    "update_profile": _update_profile,
    "convert_transaction": _convert,
    **RECURRING_PREVIEW_BUILDERS,
    **INVESTMENT_PREVIEW_BUILDERS,
    **EXPORT_PREVIEW_BUILDERS,
}


# Previews without a dedicated card in the chat UI: the card lists ``arguments``.
_SUMMARIZED_ENTITIES = frozenset(
    {
        "recurring_transaction",
        "profile",
        CONVERSION_ENTITY_TYPE,
        "investment_transaction",
        "data_export",
    }
)
_HIDDEN_KEYS = frozenset({"made_from", "response_language", "default_account_id"})
_RENAMED_KEYS = {"from_acc": "from_account", "to_acc": "to_account"}


def readable_arguments(preview: dict) -> dict:
    """What the approval changes, with names instead of ids, for a generic card.

    Without it the in-app chat card shows only the message, so the user could
    approve a change without seeing it.
    """
    operation = preview.get("operation")
    if operation == "skip":
        data: dict[str, Any] = {"skipped_date": preview.get("skipped_date")}
    elif operation == "delete":
        data = {"delete_generated": preview.get("delete_generated")}
    elif operation == "scan":
        data = {
            "use_ai": preview.get("use_ai"),
            "scans_remaining": preview.get("scans_remaining"),
        }
    elif operation == "export":
        data = {"replaces": preview.get("replaces")}
    elif operation == "convert":
        data = {
            "from_type": preview.get("from_type"),
            "to_type": preview.get("to_type"),
            **(preview.get("fields") or {}),
        }
    else:
        data = dict(preview.get("changes") or preview.get("fields") or {})
    if preview.get("total_amount") is not None:
        data["total_amount"] = preview["total_amount"]

    labels = preview.get("labels") or {}
    readable: dict[str, Any] = {}
    for key, value in data.items():
        if key in _HIDDEN_KEYS or value is None:
            continue
        if key in labels:
            key, value = _RENAMED_KEYS.get(key, key.removesuffix("_id")), labels[key]
        readable[key] = value
    return readable


def _without_internal_args(tool_args: dict) -> dict:
    return {
        key: value for key, value in tool_args.items() if key != "response_language"
    }


async def build_pending_preview(
    db: AsyncSession, user: models.User, tool_name: str, tool_args: dict
) -> dict:
    """Structured preview for the chat UI (read-only deletes, editable creates/updates)."""
    tool_args = _without_internal_args(tool_args)
    builder = PREVIEW_BUILDERS.get(tool_name)
    if builder is None:
        return {"kind": tool_name, "operation": "other", "arguments": tool_args}
    preview = await builder(db, user, tool_name, tool_args)
    if preview.get("entity_type") in _SUMMARIZED_ENTITIES:
        preview["arguments"] = readable_arguments(preview)
    if "existing" in preview:
        preview["record_version"] = record_version(preview["existing"])
    return preview
