"""Pydantic AI finance agent with Vercel AI SDK streaming.

Replaces the hand-rolled OpenRouter tool-calling loop in ``chat_agent.py``.
The agent is driven by ``VercelAIAdapter`` (see ``api/api_v1/endpoints/chat.py``)
which speaks the Vercel AI UI Message Stream Protocol so the React frontend can
use ``@ai-sdk/react``'s ``useChat``.

Key mappings from the old harness:
- OpenRouter client            -> ``OpenRouterModel`` + ``OpenRouterProvider``
- ``execute_tool`` dispatch    -> typed tool functions on a ``FunctionToolset``
- read-only data tools         -> each emits a ``DataChunk`` (one per
  frontend component id) via ``ToolReturn(metadata=[...])``
- ``ask_user`` tool            -> emits a ``data-pending-question`` ``DataChunk``;
  the frontend renders chips and sends the answer as the next user message
- ``/confirm`` + Redis pending -> write/destructive tools do NOT execute during
  the agent run; they emit a ``data-pending-confirmation`` ``DataChunk``
  (built from ``build_pending_preview`` + stashed in Redis via
  ``store_pending_action``). The frontend renders the editable approval card
  from that DataChunk and ``POST /conversations/{id}/confirm`` runs
  ``execute_tool`` with the (possibly edited) args. The v6 tool-approval
  protocol was rejected because it can't carry edited args back.
- per-round / per-tool telemetry -> ``Hooks`` capability
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi_pagination import Params
from pydantic_ai import (
    Agent,
    FunctionToolset,
    RunContext,
    ToolDefinition,
    ToolReturn,
)
from pydantic_ai.capabilities import Hooks
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.ui.vercel_ai.response_types import DataChunk, SourceUrlChunk
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.ai.chat_tools import (
    LIST_LIMIT,
    ToolExecutionError,
    _slim_account,
    _slim_category,
    _slim_place,
    build_pending_preview,
    build_system_prompt,
    format_confirmation_message,
    load_bootstrap_context,
)
from app.ai.tools import (
    AskUserArgs,
    BulkCreateArgs,
    BulkDeleteArgs,
    CreateBalanceAdjustmentArgs,
    CreateExpenseArgs,
    CreateIncomeArgs,
    CreateTransferArgs,
    DeleteTransactionArgs,
    GetPeriodArgs,
    ListBalanceAdjustmentsArgs,
    ManageAccountArgs,
    ManageCategoryArgs,
    ManagePlaceArgs,
    ManageSubcategoryArgs,
    RenderDataArgs,
    SearchTransactionsArgs,
    SearchWebArgs,
    SubmitFeedbackArgs,
    UpdateTransactionArgs,
)
from app.ai.web_search import search_web as _search_web
from app.api.deps import DateFilterType
from app.core.config import settings
from app.core.tiers import (
    FEATURE_ADVANCED_ANALYTICS,
    FEATURE_BULK_OPERATIONS,
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
from app.utilities.observability import record_llm_call, record_tool_call
from app.utilities.redis import store_pending_action

# Tool errors surfaced to the model as a normal (non-fatal) tool result rather
# than aborting the whole run (matches the old ``execute_tool`` try/except).
_TOOL_ERRORS = (ServiceError, ToolExecutionError, ValueError, KeyError)


@dataclass
class ChatDeps:
    """Per-request dependencies injected into every tool and instruction call."""

    db: AsyncSession
    user: models.User
    tier: Tier
    conversation_id: str
    local_date: date = field(default_factory=date.today)
    time_zone: str = "UTC"
    locale: str = "en"
    turn_id: int | None = None
    # Set to True by ``_defer_for_confirmation`` when a write/destructive tool
    # produced a confirmation draft this turn. Read by the endpoint's
    # ``on_complete`` to flag the observability turn as ``pending_confirmation``.
    had_pending_confirmation: bool = False


def build_model() -> OpenRouterModel:
    return OpenRouterModel(
        settings.CHAT_MODEL or settings.OPENROUTER_MODEL,
        provider=OpenRouterProvider(
            api_key=settings.OPENROUTER_API_KEY,
            app_url=settings.OPENROUTER_SITE_URL,
            app_title=settings.OPENROUTER_APP_NAME,
        ),
    )


async def chat_instructions(ctx: RunContext[ChatDeps]) -> str:
    """Dynamic, server-owned instructions (never persisted to the client)."""
    bootstrap = await load_bootstrap_context(ctx.deps.db, ctx.deps.user)
    return build_system_prompt(
        bootstrap,
        ctx.deps.tier,
        local_date=ctx.deps.local_date,
        time_zone=ctx.deps.time_zone,
    )


agent = Agent(
    build_model(),
    deps_type=ChatDeps,
    output_type=str,
    instructions=chat_instructions,
    retries=1,
)


def get_chat_agent() -> Agent[ChatDeps, str]:
    return agent


# --------------------------------------------------------------------------- #
# Tool functions
# --------------------------------------------------------------------------- #
# Read-only "data" tools return a ToolReturn carrying a DataChunk whose `type`
# matches a frontend render component (e.g. "data-accounts-list"). The
# `return_value` is a short summary the model sees so it can reply briefly
# without repeating the rendered data.
#
# Write / destructive tools return a plain string. Approval is gated dynamically
# by ``ApprovalRequiredToolset`` (see ``build_toolset``) which replicates the old
# ``requires_confirmation`` logic.


async def get_user_profile(ctx: RunContext[ChatDeps]) -> str:
    """Get the current user's profile (country, name)."""
    user = ctx.deps.user
    return f"User: {user.name}, country {user.country}."


async def list_accounts(ctx: RunContext[ChatDeps]) -> ToolReturn:
    """List all accounts for the user with their balances."""
    accounts = await account_service.list_accounts(
        ctx.deps.db, ctx.deps.user, skip=0, limit=LIST_LIMIT
    )
    items = [_slim_account(a) for a in accounts]
    return ToolReturn(
        return_value=f"Found {len(items)} accounts.",
        metadata=[DataChunk(type="data-accounts-list", data={"items": items})],
    )


async def list_categories(ctx: RunContext[ChatDeps]) -> ToolReturn:
    """List categories with nested subcategories."""
    categories = await catalog_service.list_categories(
        ctx.deps.db, ctx.deps.user, skip=0, limit=LIST_LIMIT
    )
    items = [_slim_category(c) for c in categories]
    return ToolReturn(
        return_value=f"Found {len(items)} categories.",
        metadata=[DataChunk(type="data-categories-list", data={"items": items})],
    )


async def list_places(ctx: RunContext[ChatDeps]) -> ToolReturn:
    """List places (merchants/locations)."""
    places = await catalog_service.list_places(
        ctx.deps.db, ctx.deps.user, skip=0, limit=LIST_LIMIT
    )
    items = [_slim_place(p) for p in places]
    return ToolReturn(
        return_value=f"Found {len(items)} places.",
        metadata=[DataChunk(type="data-places-list", data={"items": items})],
    )


async def _defer_for_confirmation(
    ctx: RunContext[ChatDeps],
    tool_name: str,
    tool_args: dict[str, Any],
    response_language: str,
) -> ToolReturn:
    """Stash a pending action in Redis and emit a ``data-pending-confirmation``
    DataChunk so the frontend can render the editable approval card.

    The tool does NOT execute the mutation; that happens in
    ``POST /conversations/{id}/confirm`` via ``execute_tool`` once the user
    approves (possibly with edited args). Reuses ``build_pending_preview`` so the
    card gets the existing record + human-readable labels for update/delete.
    """
    db, user = ctx.deps.db, ctx.deps.user
    preview = await build_pending_preview(db, user, tool_name, tool_args)
    message = format_confirmation_message(tool_name, preview, response_language)
    action_id = str(uuid.uuid4())
    await store_pending_action(
        action_id=action_id,
        user_id=user.id,
        conversation_id=ctx.deps.conversation_id,
        tool_name=tool_name,
        tool_args=tool_args,
        preview=preview,
        response_language=response_language,
        turn_id=ctx.deps.turn_id,
    )
    ctx.deps.had_pending_confirmation = True
    return ToolReturn(
        return_value=message,
        metadata=[
            DataChunk(
                type="data-pending-confirmation",
                data={
                    "action_id": action_id,
                    "tool_name": tool_name,
                    "preview": preview,
                    "message": message,
                    "response_language": response_language,
                },
            )
        ],
    )


async def manage_account(
    ctx: RunContext[ChatDeps], payload: ManageAccountArgs
) -> ToolReturn | str:
    """Create, update, or delete an account. `id` is required for update/delete.
    Create and update require `data` with the account fields.
    `data` fields for create are: name (str), initial_balance (float).
    Creates save immediately; updates/deletes require approval."""
    if payload.action == "create":
        data = payload.data.model_dump(exclude_none=True) if payload.data else {}
        created = await account_service.create_account(
            ctx.deps.db, ctx.deps.user, schemas.AccountCreate(**data)
        )
        return f"Account {created.name} created successfully."
    return await _defer_for_confirmation(
        ctx,
        "manage_account",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def manage_category(
    ctx: RunContext[ChatDeps], payload: ManageCategoryArgs
) -> ToolReturn | str:
    """Create a category immediately; updates/deletes require approval."""
    if payload.action == "create":
        data = payload.data.model_dump(exclude_none=True) if payload.data else {}
        created = await catalog_service.create_category(
            ctx.deps.db, ctx.deps.user, schemas.CategoryCreate(**data)
        )
        return f"Category {created.name} created successfully."
    return await _defer_for_confirmation(
        ctx,
        "manage_category",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def manage_subcategory(
    ctx: RunContext[ChatDeps], payload: ManageSubcategoryArgs
) -> ToolReturn | str:
    """Create a subcategory immediately; updates/deletes require approval."""
    if payload.action == "create":
        data = payload.data.model_dump(exclude_none=True) if payload.data else {}
        created = await catalog_service.create_subcategory(
            ctx.deps.db, ctx.deps.user, schemas.SubcategoryCreate(**data)
        )
        return f"Subcategory {created.name} created successfully."
    return await _defer_for_confirmation(
        ctx,
        "manage_subcategory",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def manage_place(
    ctx: RunContext[ChatDeps], payload: ManagePlaceArgs
) -> ToolReturn | str:
    """Create a place immediately; updates/deletes require approval."""
    if payload.action == "create":
        data = payload.data.model_dump(exclude_none=True) if payload.data else {}
        created = await catalog_service.create_place(
            ctx.deps.db, ctx.deps.user, schemas.PlaceCreate(**data)
        )
        return f"Place {created.name} created successfully."
    return await _defer_for_confirmation(
        ctx,
        "manage_place",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def create_expense(
    ctx: RunContext[ChatDeps], payload: CreateExpenseArgs
) -> str:
    """Create an expense. `amount` is required. If no date is provided, today's
    date is used. Saves immediately."""
    data = payload.model_dump(exclude_none=True)
    data.setdefault("date", ctx.deps.local_date.isoformat())
    data["made_from"] = "Chat"
    await transaction_service.create_expense(
        ctx.deps.db, ctx.deps.user, schemas.ExpenseCreate(**data)
    )
    return "Expense created successfully."


async def update_expense(
    ctx: RunContext[ChatDeps], payload: UpdateTransactionArgs
) -> ToolReturn:
    """Update an expense by id. User must confirm changes in chat before saving."""
    return await _defer_for_confirmation(
        ctx,
        "update_expense",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def delete_expense(
    ctx: RunContext[ChatDeps], payload: DeleteTransactionArgs
) -> ToolReturn:
    """Permanently delete an expense by id. Requires user confirmation in chat."""
    return await _defer_for_confirmation(
        ctx,
        "delete_expense",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def create_income(
    ctx: RunContext[ChatDeps], payload: CreateIncomeArgs
) -> str:
    """Create an income. `amount` is required. If no date is provided, today's
    date is used. Saves immediately."""
    data = payload.model_dump(exclude_none=True)
    data.setdefault("date", ctx.deps.local_date.isoformat())
    data["made_from"] = "Chat"
    await transaction_service.create_income(
        ctx.deps.db, ctx.deps.user, schemas.IncomeCreate(**data)
    )
    return "Income created successfully."


async def update_income(
    ctx: RunContext[ChatDeps], payload: UpdateTransactionArgs
) -> ToolReturn:
    """Update an income by id. User must confirm changes in chat before saving."""
    return await _defer_for_confirmation(
        ctx,
        "update_income",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def delete_income(
    ctx: RunContext[ChatDeps], payload: DeleteTransactionArgs
) -> ToolReturn:
    """Permanently delete an income by id. Requires user confirmation."""
    return await _defer_for_confirmation(
        ctx,
        "delete_income",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def create_transfer(
    ctx: RunContext[ChatDeps], payload: CreateTransferArgs
) -> ToolReturn:
    """Transfer money between accounts. User must confirm in chat before saving."""
    data = payload.model_dump(exclude_none=True)
    data.setdefault("date", ctx.deps.local_date.isoformat())
    return await _defer_for_confirmation(
        ctx, "create_transfer", data, payload.response_language
    )


async def update_transfer(
    ctx: RunContext[ChatDeps], payload: UpdateTransactionArgs
) -> ToolReturn:
    """Update a transfer by id. User must confirm changes in chat before saving."""
    return await _defer_for_confirmation(
        ctx,
        "update_transfer",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def delete_transfer(
    ctx: RunContext[ChatDeps], payload: DeleteTransactionArgs
) -> ToolReturn:
    """Delete a transfer by id. Requires user confirmation."""
    return await _defer_for_confirmation(
        ctx,
        "delete_transfer",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


def _transaction_model_reference(item: dict[str, Any]) -> dict[str, Any]:
    """Return compact private context for resolving transaction follow-ups."""
    reference = {
        key: item.get(key)
        for key in ("id", "type", "amount", "date", "description")
    }
    for relationship in ("account", "category", "subcategory", "place"):
        value = item.get(relationship)
        if isinstance(value, dict) and value.get("name"):
            reference[f"{relationship}_name"] = value["name"]
    return reference


async def search_transactions(
    ctx: RunContext[ChatDeps], payload: SearchTransactionsArgs
) -> ToolReturn:
    """Search expenses, incomes, and transfers with filters."""
    from datetime import date as date_cls

    db, user = ctx.deps.db, ctx.deps.user
    types = (
        [TransactionType(t) for t in payload.transaction_type]
        if payload.transaction_type
        else None
    )
    start = date_cls.fromisoformat(payload.start_date) if payload.start_date else None
    end = date_cls.fromisoformat(payload.end_date) if payload.end_date else None
    amount_op = (
        AmountOperator(payload.amount_operator) if payload.amount_operator else None
    )
    page = await transaction_service.search_transactions(
        db,
        user,
        order=OrderDirection(payload.order or "desc"),
        search=payload.search,
        amount=payload.amount,
        amount_operator=amount_op,
        start_date=start,
        end_date=end,
        accounts=payload.accounts,
        categories=payload.categories,
        places=payload.places,
        transaction_type=types,
        params=Params(page=payload.page, size=payload.limit),
    )
    items = jsonable_encoder(page.items)[: payload.limit]
    has_more = payload.page * payload.limit < page.total
    next_page = payload.page + 1 if has_more else None
    result = {
        "items": items,
        "total": page.total,
        "page": payload.page,
        "limit": payload.limit,
        "has_more": has_more,
        "next_page": next_page,
        "truncated": page.total > len(items),
    }
    return ToolReturn(
        return_value={
            "summary": (
                f"Found {page.total} transactions; returning page "
                f"{payload.page} with {len(items)} items."
            ),
            "items": [_transaction_model_reference(item) for item in items],
            "total": page.total,
            "page": payload.page,
            "limit": payload.limit,
            "has_more": has_more,
            "next_page": next_page,
            "truncated": page.total > len(items),
        },
        metadata=[DataChunk(type="data-transactions-list", data=result)],
    )


async def get_period_summary(
    ctx: RunContext[ChatDeps], payload: GetPeriodArgs
) -> ToolReturn:
    """Get the financial summary for a period. Answer spending questions from
    `period_expenses` and period-net questions from `period_net`.
    `balance.total` is the current total balance, never the requested period's
    net. Quote returned values exactly instead of recalculating them."""
    summary = await fetch_period_summary(
        ctx.deps.db,
        ctx.deps.user,
        DateFilterType(payload.date_filter_type),
        payload.date,
        payload.account_id,
    )
    summary_data = summary.model_dump()
    return ToolReturn(
        return_value=summary_data,
        metadata=[DataChunk(type="data-period-summary", data=summary_data)],
    )


async def get_period_charts(
    ctx: RunContext[ChatDeps], payload: GetPeriodArgs
) -> ToolReturn:
    """Get authoritative chart data for a period. `net.series` contains net
    values over `net.xAxis`; `income_vs_expense.series` contains the named
    income and expense values; and `categories.categories` contains spending
    totals by category. Base insights only on returned labels and values, and
    do not infer missing points or recalculate the chart data."""
    charts = await fetch_period_charts(
        ctx.deps.db,
        ctx.deps.user,
        DateFilterType(payload.date_filter_type),
        payload.date,
        payload.account_id,
    )
    charts_data = charts.model_dump()
    return ToolReturn(
        return_value=charts_data,
        metadata=[DataChunk(type="data-charts", data=charts_data)],
    )


async def bulk_create_transactions(
    ctx: RunContext[ChatDeps], payload: BulkCreateArgs
) -> ToolReturn:
    """Create multiple incomes and/or expenses at once. Requires confirmation."""
    incomes = [i.model_dump(exclude_none=True) for i in (payload.incomes or [])]
    expenses = [e.model_dump(exclude_none=True) for e in (payload.expenses or [])]
    for item in [*incomes, *expenses]:
        item.setdefault("date", ctx.deps.local_date.isoformat())
    return await _defer_for_confirmation(
        ctx,
        "bulk_create_transactions",
        {
            "incomes": incomes,
            "expenses": expenses,
            "response_language": payload.response_language,
        },
        payload.response_language,
    )


async def bulk_delete_transactions(
    ctx: RunContext[ChatDeps], payload: BulkDeleteArgs
) -> ToolReturn:
    """Delete multiple incomes/expenses by id. Requires confirmation."""
    return await _defer_for_confirmation(
        ctx,
        "bulk_delete_transactions",
        payload.model_dump(exclude_none=True),
        payload.response_language,
    )


async def create_balance_adjustment(
    ctx: RunContext[ChatDeps], payload: CreateBalanceAdjustmentArgs
) -> ToolReturn:
    """Set an account balance to a new value. Requires confirmation."""
    data = payload.model_dump(exclude_none=True)
    data.setdefault("adjustment_date", ctx.deps.local_date.isoformat())
    return await _defer_for_confirmation(
        ctx, "create_balance_adjustment", data, payload.response_language
    )


async def list_balance_adjustments(
    ctx: RunContext[ChatDeps], payload: ListBalanceAdjustmentsArgs
) -> ToolReturn:
    """List balance adjustments, optionally for one account."""
    if payload.account_id:
        items = await balance_adjustment_service.list_balance_adjustments_by_account(
            ctx.deps.db, ctx.deps.user, payload.account_id
        )
    else:
        items = await balance_adjustment_service.list_balance_adjustments(
            ctx.deps.db, ctx.deps.user
        )
    result = {"items": jsonable_encoder(items)}
    return ToolReturn(
        return_value=f"Found {len(result['items'])} balance adjustments.",
        metadata=[DataChunk(type="data-balance-adjustments-list", data=result)],
    )


async def submit_feedback(
    ctx: RunContext[ChatDeps], payload: SubmitFeedbackArgs
) -> str:
    """Submit user feedback to the team."""
    obj = schemas.FeedbackCreate(
        message=payload.message, sentiment=payload.sentiment
    )
    await feedback_service.submit_feedback(ctx.deps.db, ctx.deps.user, obj)
    return "Feedback submitted. Thank you!"


async def search_web(
    ctx: RunContext[ChatDeps],  # noqa: ARG001
    payload: SearchWebArgs,
) -> ToolReturn:
    """Search the web for current finance information: market data, rates, news,
    regulations, definitions, and comparisons. Use for anything not in the user's
    personal data."""
    data = await _search_web(
        payload.query, topic=payload.topic, max_results=payload.max_results
    )
    results = data.get("results", []) if isinstance(data, dict) else []
    chunks: list[Any] = [
        DataChunk(
            type="data-web-sources",
            data={
                "items": results,
                "answer": data.get("answer") if isinstance(data, dict) else None,
            },
        )
    ]
    for item in results:
        url = item.get("url") or ""
        if url:
            chunks.append(
                SourceUrlChunk(sourceId=url, url=url, title=item.get("title"))
            )
    return ToolReturn(
        return_value=f"Web search returned {len(results)} results.",
        metadata=chunks,
    )


async def ask_user(
    ctx: RunContext[ChatDeps],  # noqa: ARG001
    payload: AskUserArgs,
) -> ToolReturn:
    """Ask the user a clarifying question with clickable options when the request
    is ambiguous. The turn ends until the user answers."""
    result = {
        "question": payload.question,
        "options": [o.model_dump() for o in payload.options],
        "allow_multiple": payload.allow_multiple,
        "response_language": payload.response_language,
    }
    return ToolReturn(
        return_value=payload.question,
        metadata=[DataChunk(type="data-pending-question", data=result)],
    )


async def render_data(
    ctx: RunContext[ChatDeps],  # noqa: ARG001
    payload: RenderDataArgs,
) -> ToolReturn:
    """Render a model-populated UI component. Use `data_table` for
    {title?, columns: [string], rows: [[string|number]]} and `stat_cards` for
    {items: [{label, value, sub?}]}."""
    return ToolReturn(
        return_value="UI component rendered.",
        metadata=[DataChunk(type=f"data-{payload.component}", data=payload.data)],
    )


# --------------------------------------------------------------------------- #
# Tier-gated toolset
# --------------------------------------------------------------------------- #
# Write/destructive tools don't execute during the run; they emit a
# ``data-pending-confirmation`` DataChunk (see ``_defer_for_confirmation``) and
# the user confirms via ``POST /conversations/{id}/confirm`` which runs
# ``execute_tool``. No v6 approval gating (the v6 protocol can't carry edited
# args back).


_CORE_TOOLS = (
    get_user_profile,
    list_accounts,
    manage_account,
    list_categories,
    manage_category,
    manage_subcategory,
    list_places,
    manage_place,
    create_expense,
    update_expense,
    delete_expense,
    create_income,
    update_income,
    delete_income,
    create_transfer,
    update_transfer,
    delete_transfer,
    search_transactions,
    create_balance_adjustment,
    list_balance_adjustments,
    submit_feedback,
    ask_user,
    render_data,
)


def build_toolset(tier: Tier) -> FunctionToolset[ChatDeps]:
    """Build the tier-gated toolset for a run."""
    ts: FunctionToolset[ChatDeps] = FunctionToolset()
    for fn in _CORE_TOOLS:
        ts.add_function(fn, takes_ctx=True)
    if has_feature(tier, FEATURE_ADVANCED_ANALYTICS):
        ts.add_function(get_period_summary, takes_ctx=True)
        ts.add_function(get_period_charts, takes_ctx=True)
    if has_feature(tier, FEATURE_BULK_OPERATIONS):
        ts.add_function(bulk_create_transactions, takes_ctx=True)
        ts.add_function(bulk_delete_transactions, takes_ctx=True)
    if settings.TAVILY_API_KEY and has_feature(tier, FEATURE_SEARCH_WEB):
        ts.add_function(search_web, takes_ctx=True)
    return ts


# --------------------------------------------------------------------------- #
# Observability hooks
# --------------------------------------------------------------------------- #


def _args_as_dict(args: Any) -> dict[str, Any] | None:
    if args is None:
        return None
    dump = getattr(args, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")  # type: ignore[no-any-return]
        except Exception:
            pass
    try:
        encoded = jsonable_encoder(args)
        return encoded if isinstance(encoded, dict) else {"value": encoded}
    except Exception:
        return None


async def _obs_tool_execute(
    ctx: RunContext[ChatDeps],
    *,
    call: Any,
    tool_def: ToolDefinition,  # noqa: ARG001
    args: Any,
    handler: Any,
) -> Any:
    started = time.perf_counter()
    try:
        result = await handler(args)
    except _TOOL_ERRORS as e:
        duration_ms = int((time.perf_counter() - started) * 1000)
        await record_tool_call(
            turn_id=ctx.deps.turn_id,
            round_index=ctx.run_step,
            tool_name=call.tool_name,
            arguments=_args_as_dict(args),
            result=None,
            status="failed",
            duration_ms=duration_ms,
            error=str(e),
        )
        return f"Error: {e}"
    except Exception as e:
        duration_ms = int((time.perf_counter() - started) * 1000)
        await record_tool_call(
            turn_id=ctx.deps.turn_id,
            round_index=ctx.run_step,
            tool_name=call.tool_name,
            arguments=_args_as_dict(args),
            result=None,
            status="failed",
            duration_ms=duration_ms,
            error=str(e),
        )
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    await record_tool_call(
        turn_id=ctx.deps.turn_id,
        round_index=ctx.run_step,
        tool_name=call.tool_name,
        arguments=_args_as_dict(args),
        result={"ok": True, "preview": str(result)[:500]},
        status="completed",
        duration_ms=duration_ms,
    )
    return result


async def _obs_after_model(
    ctx: RunContext[ChatDeps], *, request_context: Any,  # noqa: ARG001
    response: Any
) -> Any:
    usage = response.usage
    await record_llm_call(
        turn_id=ctx.deps.turn_id,
        round_index=ctx.run_step,
        model=response.model_name or (settings.CHAT_MODEL or settings.OPENROUTER_MODEL),
        prompt_tokens=usage.input_tokens,
        completion_tokens=usage.output_tokens,
        total_tokens=usage.input_tokens + usage.output_tokens,
        cached_tokens=usage.cache_read_tokens,
        finish_reason=response.finish_reason,
        had_tool_calls=bool(response.tool_calls),
    )
    return response


def build_obs_hooks() -> Hooks:
    return Hooks(
        tool_execute=_obs_tool_execute,
        after_model_request=_obs_after_model,
    )
