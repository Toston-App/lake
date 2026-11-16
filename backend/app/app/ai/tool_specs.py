"""The tools the in-app chat and the MCP server both offer, described once.

Each ``ToolSpec`` holds what both surfaces need: the summary the model reads,
whether the tool only reads, how a missing date is filled in, which feature
flag hides it, and (chat only) the UI data a read emits when the user should
see it. ``TOOL_ARG_MODELS`` holds the argument models, ``dispatch.HANDLERS``
the implementations and ``confirmation`` the approval policy, so a tool is
added by one spec, one model and one handler.

Chat-only tools (``ask_user``, ``render_data``, ``search_web``,
``submit_feedback``) and MCP-only ones (``confirm_action``, ``cancel_action``)
are defined where they are used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.ai.confirmation import (
    DESTRUCTIVE_TOOLS,
    MANAGE_DELETE_ACTIONS,
    requires_confirmation,
)
from app.core.config import settings
from app.services import r2


def _investments_enabled() -> bool:
    return settings.INVESTMENTS_ENABLED


def _exports_enabled() -> bool:
    # Exports are stored in R2; without it every export fails.
    return r2.is_configured()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    summary: str
    read_only: bool
    # The field a missing date defaults into (today in the user's zone).
    date_field: str | None = None
    # Every income and expense item gets a date when it has none.
    bulk_dates: bool = False
    # A new recurring series takes the user's time zone when it names none.
    series_zone: bool = False
    # Hidden (and refused) while this returns False.
    available: Callable[[], bool] | None = None
    # Chat: the UI data part a read emits when the model asks to show it.
    ui_chunk: str | None = None

    @property
    def enabled(self) -> bool:
        return self.available is None or self.available()

    @property
    def uses_time_zone(self) -> bool:
        return bool(self.date_field or self.bulk_dates or self.series_zone)

    @property
    def destructive(self) -> bool:
        """MCP ``destructiveHint``: the call may delete or overwrite data."""
        if self.read_only:
            return False
        return (
            self.name in DESTRUCTIVE_TOOLS
            or self.name in MANAGE_DELETE_ACTIONS
            or self.name.startswith("update_")
        )

    @property
    def description(self) -> str:
        """``summary`` plus the approval rule, derived from the policy."""
        if self.read_only:
            return self.summary
        if self.name in MANAGE_DELETE_ACTIONS:
            rule = "Creates run immediately; updates and deletes require confirmation."
        elif requires_confirmation(self.name, {}):
            rule = "Requires confirmation."
        else:
            rule = "It runs immediately."
        return f"{self.summary} {rule}"


def apply_date_defaults(
    spec: ToolSpec, args: dict[str, Any], today: date, time_zone: str | None
) -> dict[str, Any]:
    """Fill a missing date with the user's ``today``; returns new args.

    Without it, the expense and income schemas store NULL, which hides the
    record from every period summary.
    """
    if spec.date_field and not args.get(spec.date_field):
        args = {**args, spec.date_field: today.isoformat()}
    if spec.series_zone and time_zone and not args.get("timezone"):
        args = {**args, "timezone": time_zone}
    if spec.bulk_dates:
        args = dict(args)
        for key in ("incomes", "expenses"):
            items = args.get(key)
            if isinstance(items, list):
                args[key] = [
                    {**item, "date": today.isoformat()}
                    if isinstance(item, dict) and not item.get("date")
                    else item
                    for item in items
                ]
    return args


def _read(name: str, summary: str, **kwargs: Any) -> ToolSpec:
    return ToolSpec(name, summary, read_only=True, **kwargs)


def _write(name: str, summary: str, **kwargs: Any) -> ToolSpec:
    return ToolSpec(name, summary, read_only=False, **kwargs)


_PAGED = "Paged: pass page while has_more is true."

_SPECS: tuple[ToolSpec, ...] = (
    # -- reads ---------------------------------------------------------------------
    _read(
        "get_user_profile",
        "Get the user's profile: name, locale, the currency amounts are shown "
        "in, and the default account. It does not include balances.",
    ),
    _read(
        "list_accounts",
        "List the user's accounts with their balances. Use it to find the "
        f"account ids that other tools require. {_PAGED}",
        ui_chunk="data-accounts-list",
    ),
    _read(
        "list_categories",
        "List the user's categories and their subcategories. Use it to find "
        f"the category and subcategory ids that other tools require. {_PAGED}",
        ui_chunk="data-categories-list",
    ),
    _read(
        "list_places",
        "List the user's saved places (merchants or locations). Use it to find "
        f"the place ids that other tools require. {_PAGED}",
        ui_chunk="data-places-list",
    ),
    _read(
        "search_transactions",
        "Search the user's expenses, incomes and transfers by date, amount, "
        "text, account, category or place. Returned transaction ids can be "
        "used with the update_* and delete_* tools.",
        ui_chunk="data-transactions-list",
    ),
    _read(
        "get_period_summary",
        "Get totals for a period: income, expenses and balance. Answer "
        "spending questions from period_expenses and period-net questions "
        "from period_net; balance.total is the current total balance, never "
        "the period's net. Quote returned values instead of recalculating.",
        ui_chunk="data-period-summary",
    ),
    _read(
        "get_period_charts",
        "Get chart data for a period: net.series over net.xAxis, named income "
        "and expense values in income_vs_expense.series, and spending by "
        "category in categories.categories. Base insights only on returned "
        "labels and values; don't infer missing points.",
        ui_chunk="data-charts",
    ),
    _read(
        "compare_periods",
        "Compare income, expenses and net between two periods, with the change "
        "in spending per category. By default the period is compared with the "
        "one just before it (this month vs last month).",
    ),
    _read(
        "list_balance_adjustments",
        "List past balance adjustments, which are manual corrections to an "
        f"account balance. Account ids come from list_accounts. {_PAGED}",
        ui_chunk="data-balance-adjustments-list",
    ),
    _read(
        "list_recurring",
        "List the user's recurring series (subscriptions, bills, salary, "
        "regular transfers) with their schedule and next dates.",
    ),
    _read(
        "get_portfolio_summary",
        "Get the investment portfolio's value, amount invested and gain or "
        "loss in USD and MXN, plus the cash balance and, when they share a "
        "currency, net worth.",
        available=_investments_enabled,
    ),
    _read(
        "list_holdings",
        "List investment holdings (stocks, ETFs, bonds, crypto, funds) with "
        f"quantity, value, cost and gain. {_PAGED}",
        available=_investments_enabled,
    ),
    _read(
        "search_investment_assets",
        "Find an investment asset by symbol or name: assets Toston already "
        "has (asset_id) and stocks, ETFs and crypto at Yahoo Finance and "
        "CoinGecko (provider and external_id), to record a first purchase.",
        available=_investments_enabled,
    ),
    _read(
        "get_export_status",
        "Get the status of the user's latest data export and the app page "
        "where the user downloads it once it is ready.",
        available=_exports_enabled,
    ),
    _read(
        "list_investment_transactions",
        "List investment transactions (buys, sells, dividends, splits, "
        f"transfers), newest first. {_PAGED}",
        available=_investments_enabled,
    ),
    # -- catalog -------------------------------------------------------------------
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
    # -- transactions --------------------------------------------------------------
    _write(
        "create_expense",
        "Record a new expense. Account, category and place ids come from the "
        "list_* tools. Without a date, today is used.",
        date_field="date",
    ),
    _write(
        "update_expense",
        "Change fields of an existing expense. The id comes from search_transactions.",
    ),
    _write(
        "delete_expense", "Delete an expense. The id comes from search_transactions."
    ),
    _write(
        "create_income",
        "Record a new income. Account and category ids come from the list_* "
        "tools. Without a date, today is used.",
        date_field="date",
    ),
    _write(
        "update_income",
        "Change fields of an existing income. The id comes from search_transactions.",
    ),
    _write("delete_income", "Delete an income. The id comes from search_transactions."),
    _write(
        "create_transfer",
        "Move money between two of the user's accounts. Account ids come from "
        "list_accounts. Without a date, today is used.",
        date_field="date",
    ),
    _write(
        "update_transfer",
        "Change fields of an existing transfer. The id comes from search_transactions.",
    ),
    _write(
        "delete_transfer", "Delete a transfer. The id comes from search_transactions."
    ),
    _write(
        "bulk_create_transactions",
        "Create many incomes and expenses in one call, for example when "
        "importing a list. The size is capped per call.",
        bulk_dates=True,
    ),
    _write(
        "bulk_delete_transactions",
        "Delete many incomes and expenses by id in one call. Ids come from "
        "search_transactions. The size is capped per call.",
    ),
    _write(
        "convert_transaction",
        "Change a transaction's type, e.g. an expense that was really a "
        "transfer between the user's accounts. The original is deleted and "
        "replaced; amount, date and description carry over, a category does "
        "not.",
    ),
    _write(
        "create_balance_adjustment",
        "Set an account's balance to a stated value by recording an "
        "adjustment. The account id comes from list_accounts.",
        date_field="adjustment_date",
    ),
    # -- profile, recurring, investments ---------------------------------------------
    _write(
        "update_profile",
        "Change the user's name, display currency or default account. The "
        "currency only changes how amounts are shown; nothing is converted.",
    ),
    _write(
        "create_recurring",
        "Create a recurring expense, income or transfer that Toston records "
        "on schedule. Weekdays are 0 (Monday) to 6 (Sunday); month_days -1 "
        "means the last day. Occurrences from start_date up to today are "
        "created at once.",
        series_zone=True,
    ),
    _write(
        "create_recurring_from_transaction",
        "Turn an existing transaction into a recurring series; it becomes the "
        "first occurrence and its fields are the defaults. Ids come from "
        "search_transactions or detect_recurring.",
        series_zone=True,
    ),
    _write(
        "update_recurring",
        "Change a recurring series, or pause and resume it with is_active. "
        "Only future occurrences change. The id comes from list_recurring.",
    ),
    _write(
        "skip_recurring_occurrence",
        "Skip the next occurrence of a recurring series without creating it. "
        "The id comes from list_recurring.",
    ),
    _write(
        "delete_recurring",
        "Delete a recurring series. Past transactions are kept unless "
        "delete_generated is true. The id comes from list_recurring.",
    ),
    _write(
        "detect_recurring",
        "Scan the transaction history for recurring series the user hasn't "
        "set up yet (subscriptions, bills, salary) and return suggestions. "
        "Nothing is created. Free plans get one scan.",
    ),
    _write(
        "record_investment_transaction",
        "Record a buy, sell, dividend, split or transfer. For an existing "
        "holding pass holding_id (list_holdings); for a first purchase pass "
        "account_id and the asset from search_investment_assets (asset_id, or "
        "provider and external_id), which creates the holding. Updates the "
        "holding's quantity and cost, and the cash balance if asked.",
        available=_investments_enabled,
    ),
    _write(
        "export_data",
        "Start an Excel export of all the user's data (accounts, "
        "transactions, categories, investments and more). It is built in the "
        "background; the user downloads it from the app's export page.",
        available=_exports_enabled,
    ),
)

TOOL_SPECS: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


def enabled_specs() -> list[ToolSpec]:
    """The shared tools to offer now: those whose feature is switched on."""
    return [spec for spec in TOOL_SPECS.values() if spec.enabled]
