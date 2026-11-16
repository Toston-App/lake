"""Recurring-transaction tools: handlers, confirmation previews and messages.

``dispatch`` dispatches through ``RECURRING_HANDLERS`` and ``ai.previews``
through ``RECURRING_PREVIEW_BUILDERS``. Every recurring write is staged for
confirmation (``confirmation.RISKY_CONFIRMATION_TOOLS``): creating a series
back-fills occurrences already due, the others change or remove what future
months contain, and a detection scan may use the free plan's only scan and
send descriptions to the AI provider.
"""

from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.ai.preview_labels import RECURRING_FIELDS, labels_for
from app.ai.results import Handler, Outcome, ToolContext, ok
from app.core.config import settings
from app.schemas.transaction import TransactionType
from app.services import recurring as recurring_service
from app.services import recurring_detection as detection_service
from app.services.ownership import require_owned
from app.services.rate_limits import enforce_rate_limit
from app.services.transaction_kinds import KINDS

ENTITY_TYPE = "recurring_transaction"
# Suggestions a detection result returns; each one lists its occurrences.
DETECTION_SUGGESTION_LIMIT = 25


def _detail(template: Any) -> dict:
    return jsonable_encoder(recurring_service.to_detail(template))


def _without_internal(data: BaseModel) -> dict:
    return data.model_dump(exclude_none=True, exclude={"response_language"})


# -- handlers -------------------------------------------------------------------


async def _list(
    args, db: AsyncSession, user: models.User, _ctx: ToolContext
) -> Outcome:
    templates = await recurring_service.list_recurring(db, user)
    if args.active_only:
        templates = [t for t in templates if t.is_active]
    return ok([_detail(t) for t in templates]), "recurring_listed"


async def _create(args, db, user, _ctx) -> Outcome:
    data = schemas.RecurringTransactionCreate(**_without_internal(args))
    template = await recurring_service.create_recurring(db, user, data)
    return ok(_detail(template)), "recurring_created"


async def _create_from_transaction(args, db, user, _ctx) -> Outcome:
    data = schemas.RecurringFromTransaction(**_without_internal(args))
    template = await recurring_service.create_from_transaction(db, user, data)
    return ok(_detail(template)), "recurring_created"


async def _update(args, db, user, _ctx) -> Outcome:
    template = await recurring_service.update_recurring(db, user, args.id, args.data)
    return ok(_detail(template)), "recurring_updated"


async def _skip(args, db, user, _ctx) -> Outcome:
    template = await recurring_service.skip_next(db, user, args.id)
    return ok(_detail(template)), "recurring_skipped"


async def _delete(args, db, user, _ctx) -> Outcome:
    await recurring_service.delete_recurring(
        db, user, args.id, delete_generated=args.delete_generated
    )
    return ok({"id": args.id, "deleted": True}), "recurring_deleted"


async def _detect(args, db, user, _ctx) -> Outcome:
    # Same order as the REST endpoint: a free user who used their scan gets
    # the plan error, not a rate-limit error.
    unlimited = await detection_service.check_access(db, user)
    limiter_up = await enforce_rate_limit(
        f"recurring_detect:{user.id}",
        settings.RECURRING_DETECT_RATE_LIMIT_PER_HOUR,
        fail_open=True,
    )
    scan = await detection_service.run_scan(
        db, user, use_ai=args.use_ai, ai_allowed=limiter_up, unlimited=unlimited
    )
    result = jsonable_encoder(scan.result)
    suggestions = result.get("suggestions") or []
    result["suggestions"] = suggestions[:DETECTION_SUGGESTION_LIMIT]
    result["suggestions_truncated"] = len(suggestions) > DETECTION_SUGGESTION_LIMIT
    result["next_step"] = (
        "To turn a suggestion into a series, call "
        "create_recurring_from_transaction with the transaction_id of its "
        "latest occurrence, its type as transaction_type, and its schedule."
    )
    return ok(result), "recurring_detected"


RECURRING_HANDLERS: dict[str, Handler] = {
    "list_recurring": _list,
    "create_recurring": _create,
    "create_recurring_from_transaction": _create_from_transaction,
    "update_recurring": _update,
    "skip_recurring_occurrence": _skip,
    "delete_recurring": _delete,
    "detect_recurring": _detect,
}


# -- previews -------------------------------------------------------------------


def _name_of(fields: dict) -> str:
    description = fields.get("description")
    if description:
        return str(description)
    return f"{fields.get('type', 'recurring')} of {fields.get('amount', '?')}"


def _schedule_preview(fields: dict) -> list[dict]:
    rule = {
        key: fields.get(key)
        for key in schemas.RecurringPreviewRequest.model_fields
        if fields.get(key) is not None
    }
    preview = recurring_service.preview(schemas.RecurringPreviewRequest(**rule))
    return jsonable_encoder(preview.upcoming)


def _due_now(upcoming: list[dict], timezone: str | None) -> dict:
    """How many occurrences saving creates at once (those dated up to today).

    The schedule preview only lists the first few dates, so when all of them
    are due the real number may be higher.
    """
    today = recurring_service.local_today(timezone or "UTC").isoformat()
    due = sum(1 for occurrence in upcoming if str(occurrence["date"]) <= today)
    return {
        "created_now": due,
        "created_now_is_minimum": bool(due) and due == len(upcoming),
    }


async def _new_series_preview(
    db, user, tool_name: str, fields: dict, upcoming: list[dict], **extra: Any
) -> dict:
    return {
        "kind": tool_name,
        "operation": "create",
        "entity_type": ENTITY_TYPE,
        "display_name": _name_of(fields),
        "fields": fields,
        "labels": await labels_for(db, user, fields, RECURRING_FIELDS),
        "upcoming": upcoming,
        **_due_now(upcoming, fields.get("timezone")),
        **extra,
    }


async def _preview_create(db, user, tool_name, tool_args) -> dict:
    fields = dict(tool_args)
    return await _new_series_preview(
        db, user, tool_name, fields, _schedule_preview(fields)
    )


async def _preview_from_transaction(db, user, tool_name, tool_args) -> dict:
    kind = KINDS[TransactionType(tool_args["transaction_type"])]
    transaction = await require_owned(
        db,
        user,
        kind.crud,
        tool_args["transaction_id"],
        "Transaction",
        hide_foreign=True,
        allow_superuser=False,
    )
    source = jsonable_encoder(transaction)
    # What the series copies from the transaction, then what the call overrides.
    fields = {
        "type": tool_args["transaction_type"],
        "amount": transaction.amount,
        "description": transaction.description,
        **kind.template_fields(transaction),
        **{
            k: v
            for k, v in tool_args.items()
            if k not in ("transaction_id", "transaction_type") and v is not None
        },
        "start_date": str(source.get("date"))[:10],
    }
    upcoming = _schedule_preview({**fields, "exclude_start": True})
    return await _new_series_preview(
        db, user, tool_name, fields, upcoming, source_transaction=source
    )


async def _preview_existing(db, user, tool_name, tool_args) -> dict:
    template = await recurring_service.get_recurring(db, user, tool_args["id"])
    existing = _detail(template)
    preview: dict[str, Any] = {
        "kind": tool_name,
        "entity_type": ENTITY_TYPE,
        "record_id": tool_args["id"],
        "display_name": _name_of(existing),
        "existing": existing,
    }
    if tool_name == "update_recurring":
        changes = dict(tool_args.get("data") or {})
        preview.update(
            operation="update",
            changes=changes,
            labels=await labels_for(db, user, changes, RECURRING_FIELDS),
        )
    elif tool_name == "skip_recurring_occurrence":
        upcoming = existing.get("upcoming") or []
        preview.update(
            operation="skip", skipped_date=upcoming[0]["date"] if upcoming else None
        )
    else:
        preview.update(
            operation="delete",
            delete_generated=bool(tool_args.get("delete_generated")),
        )
    return preview


async def _preview_detect(db, user, tool_name, tool_args) -> dict:
    return {
        "kind": tool_name,
        "operation": "scan",
        "entity_type": ENTITY_TYPE,
        "display_name": "your transaction history",
        "use_ai": bool(tool_args.get("use_ai")),
        # 1 or 0 on the free plan, None when scans are unlimited.
        "scans_remaining": await detection_service.scans_remaining(db, user),
    }


RECURRING_PREVIEW_BUILDERS = {
    "create_recurring": _preview_create,
    "create_recurring_from_transaction": _preview_from_transaction,
    "update_recurring": _preview_existing,
    "skip_recurring_occurrence": _preview_existing,
    "delete_recurring": _preview_existing,
    "detect_recurring": _preview_detect,
}


# -- messages -------------------------------------------------------------------

# operation -> (English, Spanish); filled with the preview's values.
_MESSAGES: dict[str, tuple[str, str]] = {
    "create": (
        "Review the new recurring series **{name}** ({frequency}), then approve to save it.",
        "Revisa la nueva serie recurrente **{name}** ({frequency}) y aprueba para guardarla.",
    ),
    "created_now": (
        " {count} occurrences already due will be created now.",
        " Se crearán ahora {count} transacciones ya vencidas.",
    ),
    "skip": (
        "Confirm skipping the next occurrence of **{name}** ({when}).",
        "Confirma que deseas omitir la próxima fecha de **{name}** ({when}).",
    ),
    "delete": (
        "Confirm deletion of the series **{name}**. Past transactions are kept.",
        "Confirma que deseas eliminar la serie **{name}**. Las transacciones pasadas se conservan.",
    ),
    "delete_generated": (
        "Confirm deletion of **{name}** and the transactions it created. This cannot be undone.",
        "Confirma que deseas eliminar **{name}** y las transacciones que creó. Esta acción no se puede deshacer.",
    ),
    "update": (
        "Review the changes to **{name}**, then approve to apply them. Only future occurrences change.",
        "Revisa los cambios de **{name}** y aprueba para aplicarlos. Solo cambian las fechas futuras.",
    ),
    "scan": (
        "Approve a scan of your history for recurring series. Nothing is created.",
        "Aprueba revisar tu historial en busca de series recurrentes. No se crea nada.",
    ),
    "scan_ai": (
        " Transaction descriptions are sent to Toston's AI provider.",
        " Las descripciones de tus transacciones se envían al proveedor de IA de Toston.",
    ),
    "scan_last_free": (
        " This uses your plan's only scan if it finds anything.",
        " Si encuentra algo, usa el único análisis de tu plan.",
    ),
}


def format_recurring_message(preview: dict, language: str) -> str:
    """The confirmation prompt for a recurring preview (``en`` or ``es``)."""
    index = 1 if language == "es" else 0

    def text(key: str, **values: Any) -> str:
        return _MESSAGES[key][index].format(**values)

    name = preview.get("display_name") or "?"
    operation = preview.get("operation")
    if operation == "create":
        message = text(
            "create",
            name=name,
            frequency=(preview.get("fields") or {}).get("frequency", ""),
        )
        due = preview.get("created_now") or 0
        if due:
            count = str(due)
            if preview.get("created_now_is_minimum"):
                count = f"al menos {due}" if index else f"At least {due}"
            message += text("created_now", count=count)
        return message
    if operation == "skip":
        return text("skip", name=name, when=preview.get("skipped_date") or "?")
    if operation == "delete":
        key = "delete_generated" if preview.get("delete_generated") else "delete"
        return text(key, name=name)
    if operation == "scan":
        message = text("scan")
        if preview.get("use_ai"):
            message += text("scan_ai")
        if preview.get("scans_remaining") == 1:
            message += text("scan_last_free")
        return message
    return text("update", name=name)
