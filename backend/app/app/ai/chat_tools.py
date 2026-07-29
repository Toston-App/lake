"""OpenAI function tools for the finance chat agent."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi_pagination import Params
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.ai.render_components import render_catalog_prompt
from app.ai.tools import TOOL_ARG_MODELS
from app.ai.web_search import search_web
from app.api.deps import DateFilterType
from app.core.config import settings
from app.core.tiers import (
    FEATURE_ADVANCED_ANALYTICS,
    FEATURE_BULK_OPERATIONS,
    FEATURE_CHARTS,
    FEATURE_SEARCH_WEB,
    Tier,
    has_feature,
)
from app.schemas.transaction import AmountOperator, OrderDirection, TransactionType
from app.services import accounts as account_service
from app.services import balance_adjustments as balance_adjustment_service
from app.services import catalog as catalog_service
from app.services import feedback as feedback_service
from app.services import transactions as transaction_service
from app.services.errors import ServiceError
from app.services.v3_dashboard import fetch_period_charts, fetch_period_summary

CHAT_MADE_FROM = "Chat"
LIST_LIMIT = 100
SEARCH_LIMIT = 50

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

TRANSACTION_WRITE_TOOLS = frozenset(
    {
        "create_expense",
        "update_expense",
        "create_income",
        "update_income",
        "create_transfer",
        "update_transfer",
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
    return tool_name in TRANSACTION_WRITE_TOOLS


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
        return {"id": record_id, "data": merged_data}
    return {**stored_args, **override}


async def _label_account(
    db: AsyncSession, user: models.User, account_id: int | None
) -> str | None:
    if account_id is None:
        return None
    account = await crud.account.get_by_id(db, owner_id=user.id, id=account_id)
    return account.name if account else f"Account #{account_id}"


async def _label_category(db: AsyncSession, category_id: int | None) -> str | None:
    if category_id is None:
        return None
    category = await crud.category.get(db=db, id=category_id)
    return category.name if category else f"Category #{category_id}"


async def _label_subcategory(
    db: AsyncSession, subcategory_id: int | None
) -> str | None:
    if subcategory_id is None:
        return None
    sub = await crud.subcategory.get(db=db, id=subcategory_id)
    return sub.name if sub else f"Subcategory #{subcategory_id}"


async def _label_place(db: AsyncSession, place_id: int | None) -> str | None:
    if place_id is None:
        return None
    place = await crud.place.get(db=db, id=place_id)
    return place.name if place else f"Place #{place_id}"


async def _expense_field_labels(
    db: AsyncSession, user: models.User, fields: dict
) -> dict[str, str]:
    labels: dict[str, str] = {}
    if fields.get("account_id") is not None:
        name = await _label_account(db, user, fields["account_id"])
        if name:
            labels["account_id"] = name
    if fields.get("category_id") is not None:
        name = await _label_category(db, fields["category_id"])
        if name:
            labels["category_id"] = name
    if fields.get("subcategory_id") is not None:
        name = await _label_subcategory(db, fields["subcategory_id"])
        if name:
            labels["subcategory_id"] = name
    if fields.get("place_id") is not None:
        name = await _label_place(db, fields["place_id"])
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
        name = await _label_subcategory(db, fields["subcategory_id"])
        if name:
            labels["subcategory_id"] = name
    if fields.get("place_id") is not None:
        name = await _label_place(db, fields["place_id"])
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


def _serialize_record(record: Any) -> dict:
    data = jsonable_encoder(record)
    if isinstance(data.get("date"), (date, datetime)):
        data["date"] = data["date"].isoformat()[:10]
    elif data.get("date"):
        data["date"] = str(data["date"])[:10]
    return data


def _parse_transaction_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if "T" in text:
            text = text.split("T", 1)[0]
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise ToolExecutionError(f"Invalid date: {value!r}") from exc
    raise ToolExecutionError(f"Invalid date: {value!r}")


def _transaction_update_data(patch: dict) -> dict:
    """Coerce chat/JSON patch fields before CRUD update (dates must be date objects)."""
    data = dict(patch)
    if "date" in data:
        data["date"] = _parse_transaction_date(data["date"])
    return data


async def build_pending_preview(
    db: AsyncSession,
    user: models.User,
    tool_name: str,
    tool_args: dict,
) -> dict:
    """Structured preview for the chat UI (read-only deletes, editable creates/updates)."""
    if tool_name == "create_expense":
        fields = dict(tool_args)
        fields.setdefault("made_from", CHAT_MADE_FROM)
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
        return {
            "kind": tool_name,
            "operation": "update",
            "transaction_type": "expense",
            "record_id": tool_args["id"],
            "fields": fields,
            "patch": patch,
            "existing": existing,
            "labels": await _expense_field_labels(db, user, fields),
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
        fields.setdefault("made_from", CHAT_MADE_FROM)
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

    return {
        "kind": tool_name,
        "operation": "delete" if is_destructive_tool(tool_name, tool_args) else "other",
        "arguments": tool_args,
    }


def format_confirmation_message(tool_name: str, preview: dict) -> str:
    operation = preview.get("operation", "other")
    tx_type = preview.get("transaction_type", "action")
    fields = preview.get("fields") or {}
    labels = preview.get("labels") or {}

    if operation == "delete":
        amount = fields.get("amount")
        desc = fields.get("description") or ""
        amt_str = f" **{amount}**" if amount is not None else ""
        return (
            f"Please confirm you want to **delete** this {tx_type}{amt_str}"
            f"{(': ' + desc) if desc else ''}. This cannot be undone."
        )

    if operation == "create":
        amount = fields.get("amount", "?")
        desc = fields.get("description") or ""
        account = labels.get("account_id") or labels.get("from_acc")
        extra = f" to **{account}**" if account else ""
        return (
            f"Please review this new **{tx_type}** of **{amount}**{extra}"
            f"{(': ' + desc) if desc else ''}. "
            "Edit the details below, then approve to save."
        )

    if operation == "update":
        record_id = preview.get("record_id", "")
        return (
            f"Please review changes to **{tx_type} #{record_id}**. "
            "Edit the fields below, then approve to apply."
        )

    return (
        f"I need your confirmation before running `{tool_name}`. "
        "Review the details below."
    )


def _slim_account(a: models.Account) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "type": str(a.type) if a.type else None,
        "current_balance": round(float(a.current_balance or 0), 2),
    }


def _slim_category(c: models.Category) -> dict:
    subs = getattr(c, "subcategories", None) or []
    return {
        "id": c.id,
        "name": c.name,
        "is_income": c.is_income,
        "subcategories": [
            {"id": s.id, "name": s.name, "category_id": s.category_id} for s in subs
        ],
    }


def _slim_place(p: models.Place) -> dict:
    return {"id": p.id, "name": p.name, "is_online": p.is_online}


async def load_bootstrap_context(db: AsyncSession, user: models.User) -> dict:
    accounts = await crud.account.get_multi_by_owner(
        db=db, owner_id=user.id, skip=0, limit=LIST_LIMIT
    )
    categories = await crud.category.get_multi_by_owner(
        db=db, owner_id=user.id, skip=0, limit=LIST_LIMIT
    )
    places = await crud.place.get_multi_by_owner(
        db=db, owner_id=user.id, skip=0, limit=LIST_LIMIT
    )
    return {
        "accounts": [_slim_account(a) for a in accounts],
        "categories": [_slim_category(c) for c in categories],
        "places": [_slim_place(p) for p in places],
        "user": {"id": user.id, "country": user.country, "name": user.name},
    }


def _ok(data: Any, action_type: str | None = None) -> dict:
    result = {"success": True, "data": jsonable_encoder(data)}
    if action_type:
        result["action_type"] = action_type
    return result


def _err(message: str) -> dict:
    return {"success": False, "error": message}


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


async def execute_tool(
    tool_name: str,
    args: dict,
    db: AsyncSession,
    user: models.User,
    *,
    confirmed: bool = False,
) -> tuple[dict, str | None]:
    """
    Run a tool. Returns (result_dict, action_type for ChatAction).
    Validates ``args`` against the typed model in ``TOOL_ARG_MODELS`` (see
    ``app/ai/tools.py``) before dispatching, then constructs the domain
    schemas (``schemas.AccountCreate`` …) which enforce the remaining
    invariants (rounding, positivity, ``made_from`` whitelisting, …).
    """
    _ = confirmed
    try:
        arg_model_cls = TOOL_ARG_MODELS.get(tool_name)
        if arg_model_cls is None:
            # Tools with no model-facing params (readers / profile).
            return await _execute_no_arg_tool(tool_name, db, user)
        try:
            arg_model = arg_model_cls(**args)
        except Exception as exc:  # ValidationError or sibling
            return _err(f"Invalid arguments for {tool_name}: {exc}"), None

        if tool_name == "manage_account":
            return await _execute_manage_account(db, user, arg_model)

        if tool_name == "manage_category":
            return await _execute_manage_category(db, user, arg_model)

        if tool_name == "manage_subcategory":
            return await _execute_manage_subcategory(db, user, arg_model)

        if tool_name == "manage_place":
            return await _execute_manage_place(db, user, arg_model)

        if tool_name == "create_expense":
            data = arg_model.model_dump(exclude_none=True)
            data.setdefault("made_from", CHAT_MADE_FROM)
            obj = schemas.ExpenseCreate(**data)
            created = await transaction_service.create_expense(db, user, obj)
            return _ok(jsonable_encoder(created)), "expense_created"

        if tool_name == "update_expense":
            obj_in = schemas.ExpenseUpdate(**(arg_model.data or {}))
            updated = await transaction_service.update_expense(
                db, user, arg_model.id, obj_in
            )
            return _ok(jsonable_encoder(updated)), "expense_updated"

        if tool_name == "delete_expense":
            await transaction_service.delete_expense(db, user, arg_model.id)
            return _ok({"id": arg_model.id, "deleted": True}), "expense_deleted"

        if tool_name == "create_income":
            data = arg_model.model_dump(exclude_none=True)
            data.setdefault("made_from", CHAT_MADE_FROM)
            obj = schemas.IncomeCreate(**data)
            created = await transaction_service.create_income(db, user, obj)
            return _ok(jsonable_encoder(created)), "income_created"

        if tool_name == "update_income":
            obj_in = schemas.IncomeUpdate(**(arg_model.data or {}))
            updated = await transaction_service.update_income(
                db, user, arg_model.id, obj_in
            )
            return _ok(jsonable_encoder(updated)), "income_updated"

        if tool_name == "delete_income":
            await transaction_service.delete_income(db, user, arg_model.id)
            return _ok({"id": arg_model.id, "deleted": True}), "income_deleted"

        if tool_name == "create_transfer":
            data = arg_model.model_dump(exclude_none=True)
            obj = schemas.TransferCreate(**data)
            created = await transaction_service.create_transfer(db, user, obj)
            return _ok(jsonable_encoder(created)), "transfer_created"

        if tool_name == "update_transfer":
            obj_in = schemas.TransferUpdate(**(arg_model.data or {}))
            updated = await transaction_service.update_transfer(
                db, user, arg_model.id, obj_in
            )
            return _ok(jsonable_encoder(updated)), "transfer_updated"

        if tool_name == "delete_transfer":
            await transaction_service.delete_transfer(db, user, arg_model.id)
            return _ok({"id": arg_model.id, "deleted": True}), "transfer_deleted"

        if tool_name == "search_transactions":
            order = OrderDirection(arg_model.order or "desc")
            amount_op = (
                AmountOperator(arg_model.amount_operator)
                if arg_model.amount_operator
                else None
            )
            types = (
                [TransactionType(t) for t in arg_model.transaction_type]
                if arg_model.transaction_type
                else None
            )
            start = arg_model.start_date
            end = arg_model.end_date
            if start and isinstance(start, str):
                start = date.fromisoformat(start)
            if end and isinstance(end, str):
                end = date.fromisoformat(end)
            page = await transaction_service.search_transactions(
                db,
                user,
                order=order,
                search=arg_model.search,
                amount=arg_model.amount,
                amount_operator=amount_op,
                start_date=start,
                end_date=end,
                accounts=arg_model.accounts,
                categories=arg_model.categories,
                places=arg_model.places,
                transaction_type=types,
                params=Params(page=1, size=arg_model.limit),
            )
            items = jsonable_encoder(page.items)[: arg_model.limit]
            return _ok(
                {
                    "items": items,
                    "total": page.total,
                    "truncated": page.total > arg_model.limit,
                }
            ), "transactions_searched"

        if tool_name == "get_period_summary":
            dft = DateFilterType(arg_model.date_filter_type)
            summary = await fetch_period_summary(
                db, user, dft, arg_model.date, arg_model.account_id
            )
            return _ok(summary.model_dump()), "summary_fetched"

        if tool_name == "get_period_charts":
            dft = DateFilterType(arg_model.date_filter_type)
            charts = await fetch_period_charts(
                db, user, dft, arg_model.date, arg_model.account_id
            )
            return _ok(charts.model_dump()), "charts_fetched"

        if tool_name == "bulk_create_transactions":
            bulk = schemas.BulkCreate(**arg_model.model_dump(exclude_none=True))
            created = await transaction_service.bulk_create_transactions(
                db, user, bulk, made_from=CHAT_MADE_FROM
            )
            return _ok(
                {
                    "incomes": jsonable_encoder(created["incomes"]),
                    "expenses": jsonable_encoder(created["expenses"]),
                }
            ), "bulk_created"

        if tool_name == "bulk_delete_transactions":
            deleted = await transaction_service.bulk_delete_transactions(
                db, user, schemas.BulkDelete(**arg_model.model_dump(exclude_none=True))
            )
            return _ok(
                {
                    "incomes": [item.id for item in deleted["incomes"]],
                    "expenses": [item.id for item in deleted["expenses"]],
                }
            ), "bulk_deleted"

        if tool_name == "create_balance_adjustment":
            obj = schemas.BalanceAdjustmentCreate(
                **arg_model.model_dump(exclude_none=True)
            )
            adjustment = await balance_adjustment_service.create_balance_adjustment(
                db, user, obj
            )
            return _ok(jsonable_encoder(adjustment)), "balance_adjustment_created"

        if tool_name == "list_balance_adjustments":
            if arg_model.account_id:
                items = await balance_adjustment_service.list_balance_adjustments_by_account(
                    db, user, arg_model.account_id
                )
            else:
                items = await balance_adjustment_service.list_balance_adjustments(
                    db, user
                )
            return _ok(jsonable_encoder(items)), "balance_adjustments_listed"

        if tool_name == "submit_feedback":
            obj = schemas.FeedbackCreate(**arg_model.model_dump(exclude_none=True))
            await feedback_service.submit_feedback(db, user, obj)
            return _ok({"submitted": True}), "feedback_submitted"

        if tool_name == "search_web":
            query = arg_model.query
            if not str(query).strip():
                return _err("query is required"), None
            if not settings.TAVILY_API_KEY:
                return _err("Web search is not configured"), None
            data = await search_web(
                str(query).strip(),
                topic=arg_model.topic,
                max_results=arg_model.max_results,
            )
            return _ok(data), "web_searched"

        return _err(f"Unknown tool: {tool_name}"), None

    except ToolExecutionError as e:
        return _err(e.message), None
    except ServiceError as e:
        return _err(e.message), None
    except Exception as e:
        return _err(str(e)), None


async def _execute_manage_account(
    db: AsyncSession, user: models.User, arg_model
) -> tuple[dict, str | None]:
    action = arg_model.action
    data = arg_model.data.model_dump(exclude_none=True) if arg_model.data else {}
    if action == "create":
        obj = schemas.AccountCreate(**data)
        created = await account_service.create_account(db, user, obj)
        return _ok(_slim_account(created)), "account_created"
    if action == "update":
        updated = await account_service.update_account(
            db, user, arg_model.id, account_in=schemas.AccountUpdate(**data)
        )
        return _ok(_slim_account(updated)), "account_updated"
    if action == "delete":
        await account_service.delete_account(db, user, arg_model.id)
        return _ok({"id": arg_model.id, "deleted": True}), "account_deleted"
    return _err("Invalid action for manage_account"), None


async def _execute_manage_category(
    db: AsyncSession, user: models.User, arg_model
) -> tuple[dict, str | None]:
    action = arg_model.action
    data = arg_model.data.model_dump(exclude_none=True) if arg_model.data else {}
    if action == "create":
        obj = schemas.CategoryCreate(**data)
        created = await catalog_service.create_category(db, user, obj)
        return _ok(jsonable_encoder(created)), "category_created"
    if action == "update":
        updated = await catalog_service.update_category(
            db, user, arg_model.id, schemas.CategoryUpdate(**data)
        )
        return _ok(jsonable_encoder(updated)), "category_updated"
    if action == "delete":
        await catalog_service.delete_category(db, user, arg_model.id)
        return _ok({"id": arg_model.id, "deleted": True}), "category_deleted"
    return _err("Invalid action for manage_category"), None


async def _execute_manage_subcategory(
    db: AsyncSession, user: models.User, arg_model
) -> tuple[dict, str | None]:
    action = arg_model.action
    data = arg_model.data.model_dump(exclude_none=True) if arg_model.data else {}
    if action == "create":
        obj = schemas.SubcategoryCreate(**data)
        created = await catalog_service.create_subcategory(db, user, obj)
        return _ok(jsonable_encoder(created)), "subcategory_created"
    if action == "update":
        updated = await catalog_service.update_subcategory(
            db=db,
            user=user,
            subcategory_id=arg_model.id,
            subcategory_in=schemas.SubcategoryUpdate(**data),
        )
        return _ok(jsonable_encoder(updated)), "subcategory_updated"
    if action == "delete":
        await catalog_service.delete_subcategory(db, user, arg_model.id)
        return _ok({"id": arg_model.id, "deleted": True}), "subcategory_deleted"
    return _err("Invalid action for manage_subcategory"), None


async def _execute_manage_place(
    db: AsyncSession, user: models.User, arg_model
) -> tuple[dict, str | None]:
    action = arg_model.action
    data = arg_model.data.model_dump(exclude_none=True) if arg_model.data else {}
    if action == "create":
        obj = schemas.PlaceCreate(**data)
        created = await catalog_service.create_place(db, user, obj)
        return _ok(_slim_place(created)), "place_created"
    if action == "update":
        updated = await catalog_service.update_place(
            db, user, arg_model.id, schemas.PlaceUpdate(**data)
        )
        return _ok(_slim_place(updated)), "place_updated"
    if action == "delete":
        await catalog_service.delete_place(db, user, arg_model.id)
        return _ok({"id": arg_model.id, "deleted": True}), "place_deleted"
    return _err("Invalid action for manage_place"), None


async def _execute_no_arg_tool(
    tool_name: str, db: AsyncSession, user: models.User
) -> tuple[dict, str | None]:
    if tool_name == "get_user_profile":
        return _ok({"country": user.country, "name": user.name, "id": user.id}), "profile_fetched"
    if tool_name == "list_accounts":
        accounts = await account_service.list_accounts(
            db, user, skip=0, limit=LIST_LIMIT
        )
        return _ok([_slim_account(a) for a in accounts]), "accounts_listed"
    if tool_name == "list_categories":
        categories = await catalog_service.list_categories(
            db, user, skip=0, limit=LIST_LIMIT
        )
        return _ok([_slim_category(c) for c in categories]), "categories_listed"
    if tool_name == "list_places":
        places = await catalog_service.list_places(
            db, user, skip=0, limit=LIST_LIMIT
        )
        return _ok([_slim_place(p) for p in places]), "places_listed"
    return _err(f"Unknown tool: {tool_name}"), None




def build_system_prompt(bootstrap: dict, tier: Tier = Tier.tier3) -> str:
    if settings.TAVILY_API_KEY and has_feature(tier, FEATURE_SEARCH_WEB):
        web_search_rule = (
            "- Use `search_web` for current or external finance information "
            "(markets, rates, news, regulations, definitions). Cite sources briefly."
        )
    else:
        web_search_rule = (
            "- Web search is unavailable; answer general finance questions from "
            "knowledge and note when data may be outdated."
        )

    if has_feature(tier, FEATURE_CHARTS):
        charts_rule = (
            "- After gathering data, call `render_ui` to choose components. Use "
            "`data_table` or `stat_cards` with inline `data` for comparisons and "
            "key figures. Use chart components (`bar_chart`, `line_chart`, "
            "`pie_chart`) for trends and distributions. Use `web_sources` after "
            "`search_web`."
        )
    else:
        charts_rule = (
            "- After gathering data, call `render_ui` to choose components. Use "
            "`data_table` or `stat_cards` with inline `data` for comparisons and "
            "key figures. Do NOT use chart components (`bar_chart`, `line_chart`, "
            "`pie_chart`); they are not available on this plan. Use `web_sources` "
            "after `search_web`."
        )

    if has_feature(tier, FEATURE_ADVANCED_ANALYTICS):
        analytics_rule = (
            "- Use `get_period_summary` and `get_period_charts` for the user's "
            "personal finance summaries and trends over a date period."
        )
    else:
        analytics_rule = (
            "- Period summaries and charts tools are not available on this plan; "
            "answer summary questions from listed transactions where possible."
        )

    if has_feature(tier, FEATURE_BULK_OPERATIONS):
        bulk_rule = "- Use bulk tools (`bulk_create_transactions`, `bulk_delete_transactions`) for multi-item operations."
    else:
        bulk_rule = "- Bulk operations tools are not available on this plan."

    return f"""You are Toston, a comprehensive finance assistant. You help with:
- Personal finances: expenses, incomes, transfers, accounts, categories, places, summaries, and charts.
- General finance: markets, rates, investing concepts, budgeting strategies, comparisons, and financial planning.

Rules:
- Use tools for all personal data and mutations. Never invent account, category, or transaction IDs.
- Match IDs only from the bootstrap lists below or from prior tool results.
- Date filter types: date (YYYY-MM-DD), week (YYYY-MM-DD start), month (YYYY-MM), quarter (YYYY-Q1), year (YYYY), range (YYYY-MM-DD:YYYY-MM-DD).
- Deletes and bulk deletes require user confirmation; when you call those tools, explain what will be deleted and wait for confirmation.
- ALL mutations (create/update/delete of transactions, accounts, categories, subcategories, places, transfers, balance adjustments, and bulk operations) return a confirmation draft for the user to review. After calling any mutation tool, STOP: do not call further tools or produce additional output until the user confirms or cancels. The user's app renders an editable approval card from the draft.
{web_search_rule}
{analytics_rule}
{bulk_rule}
- Use `ask_user` when the request is ambiguous (missing period, account, or intent). Provide 2-6 clear options instead of guessing.
- Reply in Spanish. Be concise and friendly.
- Never expose raw IDs to the user.
- Never do something else that is not finance relating, for example coding, web search for non-related topics, etc.
- When the client renders structured UI (lists, summaries, charts, tables, stats, sources), do not repeat that data in prose. Only tell user the acknowledgment, insight, or a follow-up.
- For mutations (create/update/delete), a brief confirmation is fine.
- If a tool failed, explain the error clearly.
{charts_rule}
- Render only what answers the request.
- Today is {date.today().isoformat()}.

Available UI components for render_ui:
{render_catalog_prompt()}

Bootstrap data:
{json.dumps(bootstrap, ensure_ascii=False)}
"""
