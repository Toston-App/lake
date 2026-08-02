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
``execute_tool`` (chat_tools.py) re-validates via ``TOOL_ARG_MODELS`` at
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

from typing import Any, Literal

from pydantic import BaseModel, Field

ResponseLanguage = Literal["en", "es"]


def response_language_field() -> Any:
    return Field(
        ...,
        description=(
            "Language for the user-facing approval or question UI. Use `es` "
            "for Spanish and `en` for English or any unsupported language."
        ),
    )


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


class SearchTransactionsArgs(BaseModel):
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


class GetPeriodArgs(BaseModel):
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

    incomes: list[int] | None = Field(
        default=None, description="Income ids to delete."
    )
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


class ListBalanceAdjustmentsArgs(BaseModel):
    """Arguments for ``list_balance_adjustments``."""

    account_id: int | None = Field(
        default=None, description="Optional account id to scope the listing."
    )


class SubmitFeedbackArgs(BaseModel):
    """Arguments for ``submit_feedback``."""

    message: str = Field(..., description="Feedback message from the user.")
    sentiment: str | None = Field(
        default=None, description="Optional sentiment label e.g. 'positive'."
    )


class SearchWebArgs(BaseModel):
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
    "submit_feedback": SubmitFeedbackArgs,
    "search_web": SearchWebArgs,
    "ask_user": AskUserArgs,
    "render_data": RenderDataArgs,
}
