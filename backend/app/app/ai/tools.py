"""Pydantic tool-argument models for the chat agent.

Each tool that takes parameters beyond ``RunContext`` accepts a single
``BaseModel`` argument. PydanticAI collapses the tool's JSON schema down to
that model's schema (single-parameter tool simplification — see
https://pydantic.dev/docs/ai/tools-toolsets/tools/#function-tools-and-schema)
and validates/coerces the model's call args into the model before the function
runs. Field ``description``s become the model-facing parameter descriptions.

The ``*Input`` models deliberately omit server-controlled fields
(``made_from``, ``import_id``, ``current_balance``, ``total_*``, ``color``,
``type``, ``owner_id`` …) so the model never sees fields it must not set.
``execute_tool`` (dispatch.py) re-validates via ``TOOL_ARG_MODELS`` at
confirmation time and then constructs the domain schemas
(``schemas.AccountCreate``, ``schemas.ExpenseCreate`` …) which enforce the
remaining invariants (rounding, positivity, ``made_from`` whitelisting, etc.).

The deferred-confirmation contract is preserved bit-for-bit: tool functions
serialize their payload via ``model_dump(exclude_none=True)`` into the same
flat-dict shape that ``build_pending_preview`` and ``execute_tool`` already
consume, and ``POST /conversations/{id}/confirm`` still posts
``ChatConfirmRequest.tool_args: dict[str, Any] | None``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.recurring_transaction import (
    RecurringFromTransaction,
    RecurringTransactionCreate,
    RecurringTransactionUpdate,
)

# Max rows a list tool (and a preview's choice lists) returns.
LIST_LIMIT = 100

ResponseLanguage = Literal["en", "es"]
ReadPresentation = Literal["model_only", "user_visible"]


def response_language_field() -> Any:
    return Field(
        ...,
        description=(
            "Language for the user-facing approval or question UI. Use `es` "
            "for Spanish and `en` for English or any unsupported language."
        ),
    )


def read_presentation_field() -> Any:
    return Field(
        default="model_only",
        description=(
            "Use `model_only` for intermediate lookup, filtering, calculation, "
            "ID resolution, or data supporting a concise text answer. Use "
            "`user_visible` when the user explicitly asks to see a list, summary, "
            "chart, or sources, or when the visual materially improves the answer. "
            "A scalar question such as 'how much did I spend?' should normally use "
            "`model_only`."
        ),
    )


class ReadPresentationArgs(BaseModel):
    """Shared presentation intent for read-only data tools."""

    presentation: ReadPresentation = read_presentation_field()


class AccountInput(BaseModel):
    """Model-settable account fields (excludes balances, color, type, owner)."""

    name: str | None = Field(
        default=None, description="Account name. Required when creating."
    )
    initial_balance: float | None = Field(
        default=None,
        description="Starting balance for a new account. Defaults to 0.",
    )


class CategoryInput(BaseModel):
    """Model-settable category fields."""

    name: str | None = Field(default=None, description="Category name.")
    description: str | None = Field(default=None, description="Category description.")
    color: str | None = Field(
        default=None,
        description='Hex color e.g. "#168FFF". Required when creating.',
    )
    icon: str | None = Field(default=None, description="Optional icon identifier.")
    is_income: bool | None = Field(
        default=None,
        description="True for income categories, false for expense categories.",
    )


class SubcategoryInput(BaseModel):
    """Model-settable subcategory fields."""

    name: str | None = Field(default=None, description="Subcategory name.")
    description: str | None = Field(
        default=None, description="Subcategory description."
    )
    icon: str | None = Field(default=None, description="Optional icon identifier.")
    category_id: int | None = Field(
        default=None,
        description="Parent category id. Required when creating a subcategory.",
    )


class PlaceInput(BaseModel):
    """Model-settable place fields."""

    name: str | None = Field(default=None, description="Place / merchant name.")
    is_online: bool | None = Field(
        default=None, description="Whether the place is an online merchant."
    )


class ExpenseInput(BaseModel):
    """Fields for creating an expense (no made_from / import_id)."""

    amount: float | None = Field(
        default=None, description="Expense amount. Must be positive."
    )
    date: str | None = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today.",
    )
    account_id: int | None = Field(default=None, description="Owning account id.")
    category_id: int | None = Field(default=None, description="Expense category id.")
    subcategory_id: int | None = Field(
        default=None, description="Optional subcategory id."
    )
    place_id: int | None = Field(default=None, description="Optional place id.")
    description: str | None = Field(default=None, description="Optional description.")


class IncomeInput(BaseModel):
    """Fields for creating an income (no category_id, no made_from)."""

    amount: float | None = Field(
        default=None, description="Income amount. Must be positive."
    )
    date: str | None = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today.",
    )
    account_id: int | None = Field(default=None, description="Destination account id.")
    subcategory_id: int | None = Field(
        default=None, description="Optional income subcategory id."
    )
    place_id: int | None = Field(default=None, description="Optional place id.")
    description: str | None = Field(default=None, description="Optional description.")


class TransferInput(BaseModel):
    """Fields for creating a transfer."""

    amount: float = Field(..., description="Amount to transfer. Must be positive.")
    from_acc: int = Field(..., description="Source account id.")
    to_acc: int = Field(..., description="Destination account id.")
    date: str | None = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today.",
    )
    description: str | None = Field(default=None, description="Optional description.")


class ManageAccountArgs(BaseModel):
    """Arguments for ``manage_account``."""

    action: Literal["create", "update", "delete"] = Field(
        ..., description="Whether to create, update, or delete the account."
    )
    id: int | None = Field(
        default=None,
        description="Account id. Required for update and delete.",
    )
    data: AccountInput | None = Field(
        default=None,
        description="Account fields. Required for create and update.",
    )
    response_language: ResponseLanguage = response_language_field()


class ManageCategoryArgs(BaseModel):
    """Arguments for ``manage_category``."""

    action: Literal["create", "update", "delete"] = Field(
        ..., description="Whether to create, update, or delete the category."
    )
    id: int | None = Field(
        default=None,
        description="Category id. Required for update and delete.",
    )
    data: CategoryInput | None = Field(
        default=None,
        description="Category fields. Required for create and update.",
    )
    response_language: ResponseLanguage = response_language_field()


class ManageSubcategoryArgs(BaseModel):
    """Arguments for ``manage_subcategory``."""

    action: Literal["create", "update", "delete"] = Field(
        ..., description="Whether to create, update, or delete the subcategory."
    )
    id: int | None = Field(
        default=None,
        description="Subcategory id. Required for update and delete.",
    )
    data: SubcategoryInput | None = Field(
        default=None,
        description="Subcategory fields. Required for create and update.",
    )
    response_language: ResponseLanguage = response_language_field()


class ManagePlaceArgs(BaseModel):
    """Arguments for ``manage_place``."""

    action: Literal["create", "update", "delete"] = Field(
        ..., description="Whether to create, update, or delete the place."
    )
    id: int | None = Field(
        default=None,
        description="Place id. Required for update and delete.",
    )
    data: PlaceInput | None = Field(
        default=None,
        description="Place fields. Required for create and update.",
    )
    response_language: ResponseLanguage = response_language_field()


class CreateExpenseArgs(BaseModel):
    """Arguments for ``create_expense``."""

    amount: float = Field(..., description="Expense amount. Must be positive.")
    date: str | None = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today.",
    )
    account_id: int | None = Field(default=None, description="Owning account id.")
    category_id: int | None = Field(default=None, description="Expense category id.")
    subcategory_id: int | None = Field(
        default=None, description="Optional subcategory id."
    )
    place_id: int | None = Field(default=None, description="Optional place id.")
    description: str | None = Field(default=None, description="Optional description.")


class CreateIncomeArgs(BaseModel):
    """Arguments for ``create_income``."""

    amount: float = Field(..., description="Income amount. Must be positive.")
    date: str | None = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today.",
    )
    account_id: int | None = Field(default=None, description="Destination account id.")
    subcategory_id: int | None = Field(
        default=None, description="Optional income subcategory id."
    )
    place_id: int | None = Field(default=None, description="Optional place id.")
    description: str | None = Field(default=None, description="Optional description.")


class CreateTransferArgs(BaseModel):
    """Arguments for ``create_transfer``."""

    amount: float = Field(..., description="Amount to transfer. Must be positive.")
    from_acc: int = Field(..., description="Source account id.")
    to_acc: int = Field(..., description="Destination account id.")
    date: str | None = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today.",
    )
    description: str | None = Field(default=None, description="Optional description.")
    response_language: ResponseLanguage = response_language_field()


class UpdateTransactionArgs(BaseModel):
    """Arguments for ``update_<expense|income|transfer>``.

    ``data`` is an open patch object (any subset of the transaction's fields).
    Date strings must be ISO YYYY-MM-DD.
    """

    id: int = Field(..., description="Transaction id to update.")
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Patch object with any of: amount, date, account_id, category_id, "
            "subcategory_id, place_id, from_acc, to_acc, description."
        ),
    )
    response_language: ResponseLanguage = response_language_field()


class DeleteTransactionArgs(BaseModel):
    """Arguments for ``delete_<expense|income|transfer>``."""

    id: int = Field(..., description="Transaction id to delete.")
    response_language: ResponseLanguage = response_language_field()


class SearchTransactionsArgs(ReadPresentationArgs):
    """Arguments for ``search_transactions``."""

    page: int = Field(
        default=1,
        ge=1,
        description=(
            "Page number to return. Keep the same filters and limit and increment "
            "this value while `has_more` is true when all matches are needed."
        ),
    )
    limit: int = Field(
        default=30,
        ge=1,
        le=30,
        description=(
            "Maximum number of transactions to return. When the user requests "
            "an exact count (for example, 'top 5'), set this to that count. Use "
            "30 together with successive pages when all matches are needed."
        ),
    )
    search: str | None = Field(default=None, description="Free-text search string.")
    amount: float | None = Field(
        default=None, description="Compare transaction amount against this value."
    )
    amount_operator: Literal["equal", "less", "greater"] | None = Field(
        default=None,
        description="Comparison operator for `amount`. Required when `amount` is set.",
    )
    start_date: str | None = Field(
        default=None, description="ISO date YYYY-MM-DD inclusive lower bound."
    )
    end_date: str | None = Field(
        default=None, description="ISO date YYYY-MM-DD inclusive upper bound."
    )
    accounts: list[int] | None = Field(
        default=None, description="Account ids to filter by."
    )
    categories: list[int] | None = Field(
        default=None, description="Category ids to filter by."
    )
    places: list[int] | None = Field(
        default=None, description="Place ids to filter by."
    )
    transaction_type: list[Literal["expense", "income", "transfer"]] | None = Field(
        default=None, description="Transaction types to include."
    )
    order: Literal["asc", "desc"] | None = Field(
        default=None, description="Sort direction by date. Defaults to desc."
    )


class GetPeriodArgs(ReadPresentationArgs):
    """Arguments for ``get_period_summary`` / ``get_period_charts``."""

    date_filter_type: Literal["date", "week", "month", "quarter", "year", "range"] = (
        Field(
            ...,
            description=(
                "Period kind: date (YYYY-MM-DD), week (YYYY-MM-DD start), "
                "month (YYYY-MM), quarter (YYYY-Q1), year (YYYY), "
                "range (YYYY-MM-DD:YYYY-MM-DD)."
            ),
        )
    )
    date: str = Field(
        ..., description="Period anchor value, format depends on date_filter_type."
    )
    account_id: int | None = Field(
        default=None, description="Optional account id to scope the period."
    )


class BulkCreateArgs(BaseModel):
    """Arguments for ``bulk_create_transactions``."""

    incomes: list[IncomeInput] | None = Field(
        default=None, description="Incomes to create."
    )
    expenses: list[ExpenseInput] | None = Field(
        default=None, description="Expenses to create."
    )
    response_language: ResponseLanguage = response_language_field()


class BulkDeleteArgs(BaseModel):
    """Arguments for ``bulk_delete_transactions``."""

    incomes: list[int] | None = Field(default=None, description="Income ids to delete.")
    expenses: list[int] | None = Field(
        default=None, description="Expense ids to delete."
    )
    response_language: ResponseLanguage = response_language_field()


class CreateBalanceAdjustmentArgs(BaseModel):
    """Arguments for ``create_balance_adjustment``."""

    account_id: int = Field(..., description="Account whose balance to set.")
    new_balance: float = Field(..., description="New absolute balance for the account.")
    adjustment_date: str | None = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today.",
    )
    description: str | None = Field(
        default=None, description="Optional reason for the adjustment."
    )
    response_language: ResponseLanguage = response_language_field()


class ListPageArgs(ReadPresentationArgs):
    """Arguments for the paged ``list_*`` tools."""

    page: int = Field(
        default=1,
        ge=1,
        description=(
            f"Page of {LIST_LIMIT} rows. Increment it while `has_more` is true."
        ),
    )


class ListBalanceAdjustmentsArgs(ListPageArgs):
    """Arguments for ``list_balance_adjustments``."""

    account_id: int | None = Field(
        default=None, description="Optional account id to scope the listing."
    )


class ComparePeriodsArgs(ReadPresentationArgs):
    """Arguments for ``compare_periods``."""

    date_filter_type: Literal["date", "week", "month", "quarter", "year", "range"] = (
        Field(
            ...,
            description=(
                "Kind of the main period: date (YYYY-MM-DD), week (YYYY-MM-DD "
                "start), month (YYYY-MM), quarter (YYYY-Q1), year (YYYY), "
                "range (YYYY-MM-DD:YYYY-MM-DD)."
            ),
        )
    )
    date: str = Field(
        ..., description="Main period value, format depends on date_filter_type."
    )
    compare_date_filter_type: (
        Literal["date", "week", "month", "quarter", "year", "range"] | None
    ) = Field(
        default=None,
        description=(
            "Kind of the period to compare against. Leave both compare_* fields "
            "empty to compare with the period just before the main one."
        ),
    )
    compare_date: str | None = Field(
        default=None,
        description="Value of the period to compare against, e.g. 2025-03.",
    )
    account_id: int | None = Field(
        default=None, description="Optional account id to scope both periods."
    )


class ListRecurringArgs(ReadPresentationArgs):
    """Arguments for ``list_recurring``."""

    active_only: bool = Field(
        default=False, description="Only return series that are not paused."
    )


class CreateRecurringArgs(RecurringTransactionCreate):
    """Arguments for ``create_recurring``: a new recurring series."""

    response_language: ResponseLanguage = response_language_field()


class CreateRecurringFromTransactionArgs(RecurringFromTransaction):
    """Arguments for ``create_recurring_from_transaction``."""

    response_language: ResponseLanguage = response_language_field()


class UpdateRecurringArgs(BaseModel):
    """Arguments for ``update_recurring``. Only future occurrences change."""

    id: int = Field(..., description="Recurring series id from list_recurring.")
    data: RecurringTransactionUpdate = Field(
        ...,
        description=(
            "Fields to change. Set is_active false to pause the series and "
            "true to resume it."
        ),
    )
    response_language: ResponseLanguage = response_language_field()


class RecurringIdArgs(BaseModel):
    """Arguments for ``skip_recurring_occurrence``."""

    id: int = Field(..., description="Recurring series id from list_recurring.")
    response_language: ResponseLanguage = response_language_field()


class DeleteRecurringArgs(RecurringIdArgs):
    """Arguments for ``delete_recurring``."""

    delete_generated: bool = Field(
        default=False,
        description=(
            "Also delete the transactions the series already created. The "
            "transaction the series was made from is always kept."
        ),
    )


class DetectRecurringArgs(BaseModel):
    """Arguments for ``detect_recurring``."""

    use_ai: bool = Field(
        default=False,
        description=(
            "Let Toston's AI group merchants and classify the series. Sends "
            "transaction descriptions to Toston's AI provider."
        ),
    )
    response_language: ResponseLanguage = response_language_field()


class UpdateProfileArgs(BaseModel):
    """Arguments for ``update_profile``. Omit what should stay the same."""

    name: str | None = Field(default=None, description="The user's display name.")
    currency: str | None = Field(
        default=None,
        description=(
            "ISO 4217 code of the currency amounts are shown in, e.g. EUR. It "
            "changes how amounts are shown; stored amounts are not converted."
        ),
    )
    default_account_id: int | None = Field(
        default=None,
        description="Account used when no account is given, e.g. from WhatsApp.",
    )
    response_language: ResponseLanguage = response_language_field()


class ConvertTransactionArgs(BaseModel):
    """Arguments for ``convert_transaction``."""

    from_type: Literal["expense", "income", "transfer"] = Field(
        ..., description="The transaction's current type."
    )
    id: int = Field(..., description="Transaction id from search_transactions.")
    to_type: Literal["expense", "income", "transfer"] = Field(
        ..., description="The type it should become."
    )
    account_id: int | None = Field(
        default=None,
        description=(
            "Account of the new expense or income. Defaults to the original's "
            "account (a transfer's source for an expense, destination for an income)."
        ),
    )
    from_acc: int | None = Field(
        default=None,
        description="Source account of the new transfer. Needed from an income.",
    )
    to_acc: int | None = Field(
        default=None,
        description="Destination account of the new transfer. Needed from an expense.",
    )
    category_id: int | None = Field(
        default=None,
        description="Category of the new expense. Categories never carry over.",
    )
    subcategory_id: int | None = Field(
        default=None, description="Subcategory of the new expense or income."
    )
    place_id: int | None = Field(
        default=None,
        description="Place of the new expense or income. Defaults to the original's.",
    )
    response_language: ResponseLanguage = response_language_field()


class GetPortfolioSummaryArgs(ReadPresentationArgs):
    """Arguments for ``get_portfolio_summary`` (none besides the chat's own)."""


class ListHoldingsArgs(ListPageArgs):
    """Arguments for ``list_holdings``."""

    account_id: int | None = Field(default=None, description="Only this account.")
    asset_class: Literal["equities", "fixed_income", "crypto", "funds"] | None = Field(
        default=None, description="Only this asset class."
    )
    currency: Literal["USD", "MXN"] | None = Field(
        default=None, description="Only holdings in this currency."
    )


class ListInvestmentTransactionsArgs(ListPageArgs):
    """Arguments for ``list_investment_transactions``. Use at most one filter."""

    holding_id: int | None = Field(default=None, description="Only this holding.")
    account_id: int | None = Field(default=None, description="Only this account.")
    transaction_type: (
        Literal["buy", "sell", "dividend", "split", "transfer_in", "transfer_out"]
        | None
    ) = Field(default=None, description="Only this type.")


class SearchInvestmentAssetsArgs(BaseModel):
    """Arguments for ``search_investment_assets``."""

    query: str = Field(
        ..., min_length=1, max_length=100, description="Symbol or name, e.g. AAPL."
    )
    kind: Literal["all", "stocks", "crypto"] = Field(
        default="all",
        description=(
            "stocks: USA and Mexican stocks and ETFs; crypto: cryptocurrencies."
        ),
    )


class RecordInvestmentTransactionArgs(BaseModel):
    """Arguments for ``record_investment_transaction``.

    Either an existing ``holding_id``, or an ``account_id`` and the asset: an
    ``asset_id`` Toston already has, or a ``provider`` + ``external_id`` from
    search_investment_assets (the asset and holding are created).
    """

    holding_id: int | None = Field(
        default=None, description="An existing holding, from list_holdings."
    )
    account_id: int | None = Field(
        default=None,
        description="For a new holding: the account it goes in, from list_accounts.",
    )
    asset_id: int | None = Field(
        default=None,
        description="For a new holding: an asset Toston has (search_investment_assets).",
    )
    provider: Literal["yahoo", "coingecko"] | None = Field(
        default=None,
        description="For a new asset: the provider from search_investment_assets.",
    )
    external_id: str | None = Field(
        default=None,
        max_length=128,
        description="For a new asset: its external_id from search_investment_assets.",
    )
    transaction_type: Literal[
        "buy", "sell", "dividend", "split", "transfer_in", "transfer_out"
    ] = Field(
        ...,
        description=(
            "What happened. For a split, quantity is the ratio (4 for 4:1) and "
            "price_per_unit is 0."
        ),
    )
    quantity: float = Field(
        ...,
        gt=0,
        le=1e15,
        allow_inf_nan=False,
        description="Units bought, sold or moved.",
    )
    price_per_unit: float = Field(
        ...,
        ge=0,
        le=1e15,
        allow_inf_nan=False,
        description="Price per unit in the asset's currency.",
    )
    fees: float = Field(
        default=0,
        ge=0,
        le=1e15,
        allow_inf_nan=False,
        description="Fees in the asset's currency.",
    )
    affects_cash_balance: bool = Field(
        default=False,
        description=(
            "Also move the money in or out of the account's cash balance "
            "(only for users whose currency is USD or MXN)."
        ),
    )
    executed_at: str | None = Field(
        default=None,
        description="When it happened, ISO 8601 with a time zone. Defaults to now.",
    )
    notes: str | None = Field(default=None, max_length=2000)
    response_language: ResponseLanguage = response_language_field()

    @model_validator(mode="after")
    def _one_target(self) -> RecordInvestmentTransactionArgs:
        """An existing holding, or an account plus exactly one asset identity."""
        if self.holding_id is not None:
            if any(
                v is not None
                for v in (
                    self.account_id,
                    self.asset_id,
                    self.provider,
                    self.external_id,
                )
            ):
                raise ValueError(
                    "With holding_id, leave account_id, asset_id, provider and "
                    "external_id empty"
                )
            return self
        if self.account_id is None:
            raise ValueError("Pass holding_id, or account_id for a new holding")
        external = self.provider is not None or self.external_id is not None
        if self.asset_id is not None and external:
            raise ValueError("Pass asset_id or provider+external_id, not both")
        if self.asset_id is None and not (self.provider and self.external_id):
            raise ValueError("A new holding needs asset_id or provider+external_id")
        return self

    @field_validator("executed_at")
    @classmethod
    def _aware_iso_datetime(cls, value: str | None) -> str | None:
        """Checked when the call is staged, so an approval can't fail on it."""
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("executed_at must be an ISO 8601 date and time") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("executed_at must include a time zone, e.g. +00:00")
        return value


class ExportDataArgs(BaseModel):
    """Arguments for ``export_data`` (only the approval card's language)."""

    response_language: ResponseLanguage = response_language_field()


class SubmitFeedbackArgs(BaseModel):
    """Arguments for ``submit_feedback``."""

    message: str = Field(..., description="Feedback message from the user.")
    sentiment: str | None = Field(
        default=None, description="Optional sentiment label e.g. 'positive'."
    )


class SearchWebArgs(ReadPresentationArgs):
    """Arguments for ``search_web``."""

    query: str = Field(..., description="Search query for external finance info.")
    topic: Literal["general", "news", "finance"] = Field(
        default="general", description="Search vertical."
    )
    max_results: int | None = Field(
        default=None, description="Optional cap on the number of results."
    )


class AskOption(BaseModel):
    """A clickable option presented to the user by ``ask_user``."""

    id: str = Field(..., description="Stable option identifier.")
    label: str = Field(..., description="Human-readable option label.")


class AskUserArgs(BaseModel):
    """Arguments for ``ask_user``."""

    question: str = Field(..., description="Clarifying question for the user.")
    options: list[AskOption] = Field(
        ..., description="2-6 selectable options for the user."
    )
    allow_multiple: bool = Field(
        default=False, description="Allow selecting more than one option."
    )
    response_language: ResponseLanguage = response_language_field()


class RenderDataArgs(BaseModel):
    """Arguments for ``render_data``."""

    component: Literal["data_table", "stat_cards"] = Field(
        ..., description="Frontend component id to render."
    )
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Component payload: data_table -> {title?, columns: [str], rows: [[str|number]]}; "
            "stat_cards -> {items: [{label, value, sub?}]}."
        ),
    )


TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    "manage_account": ManageAccountArgs,
    "manage_category": ManageCategoryArgs,
    "manage_subcategory": ManageSubcategoryArgs,
    "manage_place": ManagePlaceArgs,
    "create_expense": CreateExpenseArgs,
    "create_income": CreateIncomeArgs,
    "create_transfer": CreateTransferArgs,
    "update_expense": UpdateTransactionArgs,
    "update_income": UpdateTransactionArgs,
    "update_transfer": UpdateTransactionArgs,
    "delete_expense": DeleteTransactionArgs,
    "delete_income": DeleteTransactionArgs,
    "delete_transfer": DeleteTransactionArgs,
    "search_transactions": SearchTransactionsArgs,
    "get_period_summary": GetPeriodArgs,
    "get_period_charts": GetPeriodArgs,
    "bulk_create_transactions": BulkCreateArgs,
    "bulk_delete_transactions": BulkDeleteArgs,
    "create_balance_adjustment": CreateBalanceAdjustmentArgs,
    "list_balance_adjustments": ListBalanceAdjustmentsArgs,
    "list_accounts": ListPageArgs,
    "list_categories": ListPageArgs,
    "list_places": ListPageArgs,
    "update_profile": UpdateProfileArgs,
    "convert_transaction": ConvertTransactionArgs,
    "get_portfolio_summary": GetPortfolioSummaryArgs,
    "list_holdings": ListHoldingsArgs,
    "list_investment_transactions": ListInvestmentTransactionsArgs,
    "record_investment_transaction": RecordInvestmentTransactionArgs,
    "search_investment_assets": SearchInvestmentAssetsArgs,
    "export_data": ExportDataArgs,
    "compare_periods": ComparePeriodsArgs,
    "list_recurring": ListRecurringArgs,
    "create_recurring": CreateRecurringArgs,
    "create_recurring_from_transaction": CreateRecurringFromTransactionArgs,
    "update_recurring": UpdateRecurringArgs,
    "skip_recurring_occurrence": RecurringIdArgs,
    "delete_recurring": DeleteRecurringArgs,
    "detect_recurring": DetectRecurringArgs,
    "submit_feedback": SubmitFeedbackArgs,
    "search_web": SearchWebArgs,
    "ask_user": AskUserArgs,
    "render_data": RenderDataArgs,
}
