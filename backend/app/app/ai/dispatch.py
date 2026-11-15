"""Run any tool by name: the dispatcher shared by the in-app chat and MCP.

``execute_tool`` validates arguments with the tool's model in
``TOOL_ARG_MODELS`` and calls its handler from ``HANDLERS``. It never stages
or confirms anything: callers apply ``confirmation`` first.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi_pagination import Params
from pydantic import ValidationError
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.ai.confirmation import ToolExecutionError
from app.ai.export_tools import EXPORT_HANDLERS
from app.ai.investment_tools import INVESTMENT_HANDLERS
from app.ai.recurring_tools import RECURRING_HANDLERS
from app.ai.results import Handler, Outcome, ToolContext, err, ok, page_window, paged
from app.ai.tools import TOOL_ARG_MODELS
from app.ai.web_search import search_web
from app.core.config import settings
from app.schemas.made_from import MADE_FROM_CHAT
from app.schemas.transaction import AmountOperator, OrderDirection, TransactionType
from app.services import accounts as account_service
from app.services import balance_adjustments as balance_adjustment_service
from app.services import catalog as catalog_service
from app.services import feedback as feedback_service
from app.services import profile as profile_service
from app.services import transactions as transaction_service
from app.services.errors import ServiceError
from app.services.periods import DateFilterType
from app.services.transaction_conversion import ConversionTargets, convert_transaction
from app.services.v3_dashboard import (
    fetch_period_charts,
    fetch_period_comparison,
    fetch_period_summary,
)

logger = logging.getLogger(__name__)

CHAT_MADE_FROM = MADE_FROM_CHAT
SEARCH_LIMIT = 30


def slim_account(a: models.Account) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "type": str(a.type) if a.type else None,
        "current_balance": round(float(a.current_balance or 0), 2),
    }


def slim_category(c: models.Category) -> dict:
    subs = getattr(c, "subcategories", None) or []
    return {
        "id": c.id,
        "name": c.name,
        "is_income": c.is_income,
        "subcategories": [
            {"id": s.id, "name": s.name, "category_id": s.category_id} for s in subs
        ],
    }


def slim_place(p: models.Place) -> dict:
    return {"id": p.id, "name": p.name, "is_online": p.is_online}


_MISSING = object()


def _transaction_attr(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, _MISSING)
    try:
        return getattr(item, key)
    except (AttributeError, InvalidRequestError):
        return _MISSING


def _transaction_relationship(value: Any) -> dict[str, Any] | None:
    if value is _MISSING or value is None:
        return None
    result: dict[str, Any] = {}
    for key in ("id", "name", "category_id", "is_income", "is_online"):
        field_value = _transaction_attr(value, key)
        if field_value is not _MISSING:
            result[key] = field_value
    return result or None


def serialize_transaction_search_item(item: Any) -> Any:
    """Serialize one search row without walking SQLAlchemy back-references."""
    if not isinstance(item, Mapping) and not hasattr(item, "id"):
        return jsonable_encoder(item)

    result: dict[str, Any] = {}
    for key in (
        "id",
        "type",
        "amount",
        "date",
        "description",
        "account_id",
        "category_id",
        "subcategory_id",
        "place_id",
        "from_acc",
        "to_acc",
        "made_from",
    ):
        value = _transaction_attr(item, key)
        if value is not _MISSING:
            result[key] = value

    relationships = {
        "account": _transaction_attr(item, "account"),
        "category": _transaction_attr(item, "category"),
        "subcategory": _transaction_attr(item, "subcategory"),
        "place": _transaction_attr(item, "place"),
        "account_from": _transaction_attr(item, "account_from"),
        "account_to": _transaction_attr(item, "account_to"),
    }
    if relationships["category"] is _MISSING or relationships["category"] is None:
        subcategory = relationships["subcategory"]
        relationships["category"] = _transaction_attr(subcategory, "category")

    for key, relationship in relationships.items():
        summary = _transaction_relationship(relationship)
        if summary is not None:
            result[key] = summary
            if summary.get("name"):
                result[f"{key}_name"] = summary["name"]

    return jsonable_encoder(result)


async def execute_tool(
    tool_name: str,
    args: dict,
    db: AsyncSession,
    user: models.User,
    *,
    made_from: str = CHAT_MADE_FROM,
) -> tuple[dict, str | None]:
    """
    Run a tool. Returns (result_dict, action_type for ChatAction).
    Validates ``args`` against the typed model in ``TOOL_ARG_MODELS`` (see
    ``app/ai/tools.py``) before dispatching to the tool's handler, which
    constructs the domain schemas (``schemas.AccountCreate`` …) that enforce
    the remaining invariants (rounding, positivity, ``made_from`` whitelisting, …).
    """
    handler = HANDLERS.get(tool_name)
    if handler is None:
        return err(f"Unknown tool: {tool_name}"), None
    arg_model_cls = TOOL_ARG_MODELS.get(tool_name)
    try:
        arg_model = arg_model_cls(**args) if arg_model_cls else None
    except ValidationError as exc:
        return err(
            f"Invalid arguments for {tool_name}: {describe_validation_error(exc)}"
        ), None
    try:
        return await handler(arg_model, db, user, ToolContext(made_from=made_from))
    except (ToolExecutionError, ServiceError) as e:
        return err(e.message), None
    except HTTPException as e:
        # Older investment services (provider lookups, their throttle) still
        # raise FastAPI errors; their detail is a fixed, user-safe message.
        detail = e.detail if isinstance(e.detail, str) else "The tool failed."
        return err(detail), None
    except Exception:
        # Unexpected errors can quote SQL or the user's data: log them, and
        # tell the model or MCP client only that the tool failed.
        logger.exception("Tool %s failed", tool_name)
        return err("The tool failed. Please try again."), None


def describe_validation_error(exc: ValidationError) -> str:
    """Where each argument is wrong and why, without echoing the values sent."""
    return "; ".join(
        f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )


# -- catalog --------------------------------------------------------------------


def _manage(
    create, update, delete, schema_create, schema_update, slim, entity: str
) -> Handler:
    """``manage_<entity>``: create, update or delete one catalog record."""

    async def handle(args, db, user, _ctx) -> Outcome:
        data = args.data.model_dump(exclude_none=True) if args.data else {}
        if args.action == "create":
            created = await create(db, user, schema_create(**data))
            return ok(slim(created)), f"{entity}_created"
        if args.action == "update":
            updated = await update(db, user, args.id, schema_update(**data))
            return ok(slim(updated)), f"{entity}_updated"
        if args.action == "delete":
            await delete(db, user, args.id)
            return ok({"id": args.id, "deleted": True}), f"{entity}_deleted"
        return err(f"Invalid action for manage_{entity}"), None

    return handle


async def _update_subcategory(db, user, subcategory_id, subcategory_in):
    return await catalog_service.update_subcategory(
        db=db, user=user, subcategory_id=subcategory_id, subcategory_in=subcategory_in
    )


async def _update_account(db, user, account_id, account_in):
    return await account_service.update_account(
        db, user, account_id, account_in=account_in
    )


_MANAGE_HANDLERS: dict[str, Handler] = {
    "manage_account": _manage(
        account_service.create_account,
        _update_account,
        account_service.delete_account,
        schemas.AccountCreate,
        schemas.AccountUpdate,
        slim_account,
        "account",
    ),
    "manage_category": _manage(
        catalog_service.create_category,
        catalog_service.update_category,
        catalog_service.delete_category,
        schemas.CategoryCreate,
        schemas.CategoryUpdate,
        jsonable_encoder,
        "category",
    ),
    "manage_subcategory": _manage(
        catalog_service.create_subcategory,
        _update_subcategory,
        catalog_service.delete_subcategory,
        schemas.SubcategoryCreate,
        schemas.SubcategoryUpdate,
        jsonable_encoder,
        "subcategory",
    ),
    "manage_place": _manage(
        catalog_service.create_place,
        catalog_service.update_place,
        catalog_service.delete_place,
        schemas.PlaceCreate,
        schemas.PlaceUpdate,
        slim_place,
        "place",
    ),
}


# -- expenses, incomes, transfers -------------------------------------------------

_KIND_SCHEMAS = {
    "expense": (schemas.ExpenseCreate, schemas.ExpenseUpdate),
    "income": (schemas.IncomeCreate, schemas.IncomeUpdate),
    "transfer": (schemas.TransferCreate, schemas.TransferUpdate),
}


def _create_transaction(kind: str) -> Handler:
    async def handle(args, db, user, ctx) -> Outcome:
        data = args.model_dump(exclude_none=True, exclude={"response_language"})
        if kind != "transfer":
            data.setdefault("made_from", ctx.made_from)
        create = getattr(transaction_service, f"create_{kind}")
        created = await create(db, user, _KIND_SCHEMAS[kind][0](**data))
        return ok(created), f"{kind}_created"

    return handle


def _update_transaction(kind: str) -> Handler:
    async def handle(args, db, user, _ctx) -> Outcome:
        update = getattr(transaction_service, f"update_{kind}")
        updated = await update(
            db, user, args.id, _KIND_SCHEMAS[kind][1](**(args.data or {}))
        )
        return ok(updated), f"{kind}_updated"

    return handle


def _delete_transaction(kind: str) -> Handler:
    async def handle(args, db, user, _ctx) -> Outcome:
        await getattr(transaction_service, f"delete_{kind}")(db, user, args.id)
        return ok({"id": args.id, "deleted": True}), f"{kind}_deleted"

    return handle


async def _search_transactions(args, db, user, _ctx) -> Outcome:
    start, end = args.start_date, args.end_date
    if start and isinstance(start, str):
        start = date.fromisoformat(start)
    if end and isinstance(end, str):
        end = date.fromisoformat(end)
    page = await transaction_service.search_transactions(
        db,
        user,
        order=OrderDirection(args.order or "desc"),
        search=args.search,
        amount=args.amount,
        amount_operator=AmountOperator(args.amount_operator)
        if args.amount_operator
        else None,
        start_date=start,
        end_date=end,
        accounts=args.accounts,
        categories=args.categories,
        places=args.places,
        transaction_type=[TransactionType(t) for t in args.transaction_type]
        if args.transaction_type
        else None,
        params=Params(page=args.page, size=args.limit),
    )
    items = [
        serialize_transaction_search_item(item) for item in page.items[: args.limit]
    ]
    has_more = args.page * args.limit < page.total
    return ok(
        {
            "items": items,
            "total": page.total,
            "page": args.page,
            "limit": args.limit,
            "has_more": has_more,
            "next_page": args.page + 1 if has_more else None,
            "truncated": page.total > len(items),
        }
    ), "transactions_searched"


async def _bulk_create(args, db, user, ctx) -> Outcome:
    bulk = schemas.BulkCreate(
        **args.model_dump(exclude_none=True, exclude={"response_language"})
    )
    created = await transaction_service.bulk_create_transactions(
        db, user, bulk, made_from=ctx.made_from
    )
    return ok(
        {"incomes": created["incomes"], "expenses": created["expenses"]}
    ), "bulk_created"


async def _bulk_delete(args, db, user, _ctx) -> Outcome:
    deleted = await transaction_service.bulk_delete_transactions(
        db,
        user,
        schemas.BulkDelete(
            **args.model_dump(exclude_none=True, exclude={"response_language"})
        ),
    )
    return ok(
        {
            "incomes": [item.id for item in deleted["incomes"]],
            "expenses": [item.id for item in deleted["expenses"]],
        }
    ), "bulk_deleted"


async def _convert_transaction(args, db, user, ctx) -> Outcome:
    created = await convert_transaction(
        db,
        user,
        from_type=TransactionType(args.from_type),
        transaction_id=args.id,
        to_type=TransactionType(args.to_type),
        targets=ConversionTargets.from_args(args),
        made_from=ctx.made_from,
    )
    return ok(
        {
            "type": args.to_type,
            "record": created,
            "replaced": {"type": args.from_type, "id": args.id},
        }
    ), "transaction_converted"


# -- reads ----------------------------------------------------------------------


async def _period_summary(args, db, user, _ctx) -> Outcome:
    summary = await fetch_period_summary(
        db, user, DateFilterType(args.date_filter_type), args.date, args.account_id
    )
    return ok(summary.model_dump()), "summary_fetched"


async def _period_charts(args, db, user, _ctx) -> Outcome:
    charts = await fetch_period_charts(
        db, user, DateFilterType(args.date_filter_type), args.date, args.account_id
    )
    return ok(charts.model_dump()), "charts_fetched"


async def _compare_periods(args, db, user, _ctx) -> Outcome:
    comparison = await fetch_period_comparison(
        db,
        user,
        DateFilterType(args.date_filter_type),
        args.date,
        DateFilterType(args.compare_date_filter_type)
        if args.compare_date_filter_type
        else None,
        args.compare_date,
        args.account_id,
    )
    return ok(comparison), "periods_compared"


def _paged_list(fetch, slim, action: str) -> Handler:
    """A ``list_*`` tool: one page of LIST_LIMIT rows, with ``has_more``."""

    async def handle(args, db, user, _ctx) -> Outcome:
        rows = await fetch(db, user, args, **page_window(args.page))
        return paged([slim(row) for row in rows], args.page), action

    return handle


async def _balance_adjustments(db, user, args, **window):
    if args.account_id:
        return await balance_adjustment_service.list_balance_adjustments_by_account(
            db, user, args.account_id, **window
        )
    return await balance_adjustment_service.list_balance_adjustments(db, user, **window)


def _without_args(fetch):
    async def call(db, user, _args, **window):
        return await fetch(db, user, **window)

    return call


async def _user_profile(_args, _db, user, _ctx) -> Outcome:
    return ok(profile_service.profile_of(user)), "profile_fetched"


async def _update_profile(args, db, user, _ctx) -> Outcome:
    updated = await profile_service.update_profile(
        db,
        user,
        name=args.name,
        currency=args.currency,
        default_account_id=args.default_account_id,
    )
    return ok(profile_service.profile_of(updated)), "profile_updated"


# -- balances, feedback, web ------------------------------------------------------


async def _balance_adjustment(args, db, user, _ctx) -> Outcome:
    obj = schemas.BalanceAdjustmentCreate(
        **args.model_dump(exclude_none=True, exclude={"response_language"})
    )
    adjustment = await balance_adjustment_service.create_balance_adjustment(
        db, user, obj
    )
    return ok(adjustment), "balance_adjustment_created"


async def _submit_feedback(args, db, user, _ctx) -> Outcome:
    obj = schemas.FeedbackCreate(**args.model_dump(exclude_none=True))
    await feedback_service.submit_feedback(db, user, obj)
    return ok({"submitted": True}), "feedback_submitted"


async def _search_web(args, _db, _user, _ctx) -> Outcome:
    query = str(args.query).strip()
    if not query:
        return err("query is required"), None
    if not settings.TAVILY_API_KEY:
        return err("Web search is not configured"), None
    data = await search_web(query, topic=args.topic, max_results=args.max_results)
    return ok(data), "web_searched"


HANDLERS: dict[str, Handler] = {
    "get_user_profile": _user_profile,
    "update_profile": _update_profile,
    "list_accounts": _paged_list(
        _without_args(account_service.list_accounts), slim_account, "accounts_listed"
    ),
    "list_categories": _paged_list(
        _without_args(catalog_service.list_categories),
        slim_category,
        "categories_listed",
    ),
    "list_places": _paged_list(
        _without_args(catalog_service.list_places), slim_place, "places_listed"
    ),
    "list_balance_adjustments": _paged_list(
        _balance_adjustments, jsonable_encoder, "balance_adjustments_listed"
    ),
    **_MANAGE_HANDLERS,
    **{f"create_{kind}": _create_transaction(kind) for kind in _KIND_SCHEMAS},
    **{f"update_{kind}": _update_transaction(kind) for kind in _KIND_SCHEMAS},
    **{f"delete_{kind}": _delete_transaction(kind) for kind in _KIND_SCHEMAS},
    "search_transactions": _search_transactions,
    "bulk_create_transactions": _bulk_create,
    "bulk_delete_transactions": _bulk_delete,
    "convert_transaction": _convert_transaction,
    "get_period_summary": _period_summary,
    "get_period_charts": _period_charts,
    "compare_periods": _compare_periods,
    "create_balance_adjustment": _balance_adjustment,
    "submit_feedback": _submit_feedback,
    "search_web": _search_web,
    **RECURRING_HANDLERS,
    **INVESTMENT_HANDLERS,
    **EXPORT_HANDLERS,
}
