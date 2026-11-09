from datetime import date, datetime
from functools import cache
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.recurrence import (
    LAST_DAY,
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
)
from app.schemas.transaction import TransactionType

MAX_INTERVAL = 365
MAX_AMOUNT = 1_000_000_000_000
MAX_DESCRIPTION_LENGTH = 500

# The kinds the classifier reports (a habit is reported as "other").
SuggestionKind = Literal["subscription", "bill", "salary", "transfer", "other"]


@cache
def _known_timezones() -> frozenset[str]:
    return frozenset(available_timezones())


class RecurrenceRule(BaseModel):
    """Schedule fields shared by create, update and "mark as recurring"."""

    frequency: Frequency | None = None
    interval: int | None = Field(default=None, ge=1, le=MAX_INTERVAL)
    weekdays: list[int] | None = None
    monthly_mode: MonthlyMode | None = None
    month_days: list[int] | None = None
    week_of_month: int | None = None
    business_day_adjustment: BusinessDayAdjustment | None = None
    end_date: date | None = None
    max_occurrences: int | None = Field(default=None, ge=1)
    timezone: str | None = None

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if any(day < 0 or day > 6 for day in v):
            raise ValueError("weekdays must be between 0 (Monday) and 6 (Sunday)")
        return sorted(set(v))

    @field_validator("month_days")
    @classmethod
    def validate_month_days(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if any(day != LAST_DAY and (day < 1 or day > 31) for day in v):
            raise ValueError(
                "month_days must be between 1 and 31, or -1 for the last day"
            )
        return list(dict.fromkeys(v))

    @field_validator("week_of_month")
    @classmethod
    def validate_week_of_month(cls, v: int | None) -> int | None:
        if v is not None and v not in (1, 2, 3, 4, LAST_DAY):
            raise ValueError("week_of_month must be 1-4, or -1 for the last week")
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        # Only IANA names: ZoneInfo also loads files such as "posixrules"
        # that Postgres may not know, and the worker's due query evaluates
        # every template's timezone in SQL.
        if v is not None and v not in _known_timezones():
            raise ValueError("Unknown timezone")
        return v


class TemplateFields(BaseModel):
    """What every generated transaction copies from its template."""

    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    amount: float | None = Field(default=None, gt=0, le=MAX_AMOUNT, allow_inf_nan=False)
    account_id: int | None = None
    to_account_id: int | None = None
    category_id: int | None = None
    subcategory_id: int | None = None
    place_id: int | None = None

    @field_validator("amount")
    @classmethod
    def round_amount(cls, v: float | None) -> float | None:
        return round(v, 2) if v is not None else v


class RecurringTransactionFields(RecurrenceRule, TemplateFields):
    start_date: date | None = None


class RecurringTransactionCreate(RecurringTransactionFields):
    type: TransactionType
    amount: float = Field(gt=0, le=MAX_AMOUNT, allow_inf_nan=False)
    frequency: Frequency
    start_date: date

    @model_validator(mode="after")
    def validate_accounts(self) -> "RecurringTransactionCreate":
        if self.type == TransactionType.transfer:
            if not self.account_id or not self.to_account_id:
                raise ValueError("Transfers need account_id and to_account_id")
        return self


class RecurringTransactionUpdate(RecurringTransactionFields):
    is_active: bool | None = None


class RecurringFromTransaction(RecurrenceRule, TemplateFields):
    """Turn an existing transaction into a recurring template.

    Template fields default to the transaction's; any sent here override them.
    The series always starts on the transaction's date.
    """

    transaction_type: TransactionType
    transaction_id: int
    frequency: Frequency


class UpcomingOccurrence(BaseModel):
    nominal_date: date
    date: date


class RecurringTransaction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    type: TransactionType
    description: str | None = None
    amount: float
    account_id: int | None = None
    to_account_id: int | None = None
    category_id: int | None = None
    subcategory_id: int | None = None
    place_id: int | None = None
    frequency: Frequency
    interval: int
    weekdays: list[int]
    monthly_mode: MonthlyMode
    month_days: list[int]
    week_of_month: int | None = None
    business_day_adjustment: BusinessDayAdjustment
    start_date: date
    end_date: date | None = None
    max_occurrences: int | None = None
    timezone: str
    is_active: bool
    occurrences_count: int
    next_run_date: date | None = None
    next_nominal_date: date | None = None
    last_run_date: date | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RecurringTransactionDetail(RecurringTransaction):
    upcoming: list[UpcomingOccurrence] = []


class RecurringPreviewRequest(RecurrenceRule):
    frequency: Frequency
    start_date: date
    # The start date is an existing transaction (mark as recurring).
    exclude_start: bool = False


class RecurringPreview(BaseModel):
    upcoming: list[UpcomingOccurrence]


# ---------------------------------------------------------------------------
# Detection of recurring series
# ---------------------------------------------------------------------------
FINGERPRINT_LENGTH = 32
FINGERPRINT_PATTERN = r"^[0-9a-f]{32}$"
MAX_SUGGESTION_ITEMS = 50


class RecurringDetectRequest(BaseModel):
    use_ai: bool = True


class SuggestionOccurrence(BaseModel):
    transaction_id: int
    date: date
    amount: float


class RecurringSuggestion(BaseModel):
    fingerprint: str
    type: TransactionType
    merchant_name: str
    description: str | None = None
    amount: float
    amount_min: float
    amount_max: float
    account_id: int | None = None
    to_account_id: int | None = None
    category_id: int | None = None
    subcategory_id: int | None = None
    place_id: int | None = None
    frequency: Frequency
    interval: int
    weekdays: list[int] = []
    monthly_mode: MonthlyMode = MonthlyMode.day_of_month
    month_days: list[int] = []
    business_day_adjustment: BusinessDayAdjustment = BusinessDayAdjustment.none
    occurrences: list[SuggestionOccurrence]
    last_date: date
    next_expected_date: date | None = None
    confidence: float
    kind: SuggestionKind | None = None
    reasons: list[str] = []


class RecurringDetectionResult(BaseModel):
    suggestions: list[RecurringSuggestion]
    ai_used: bool
    scanned_transactions: int
    scanned_at: datetime
    # Scans left: 0 or 1 for free users, None (unlimited) for paid tiers.
    scans_remaining: int | None = None


class RecurringDetectionAccess(BaseModel):
    scans_remaining: int | None = None


class RecurringDetectionStatus(BaseModel):
    # A scan holds the claim; it stops counting once the claim goes stale.
    running: bool
    last_scan_at: datetime | None = None
    scans_remaining: int | None = None


class RecurringSuggestionAccept(RecurringFromTransaction):
    """A suggestion to turn into a template, with any edits the user made.

    `transaction_id` must be the latest occurrence of the series.
    """

    fingerprint: str = Field(
        min_length=FINGERPRINT_LENGTH,
        max_length=FINGERPRINT_LENGTH,
        pattern=FINGERPRINT_PATTERN,
    )


class RecurringSuggestionAcceptRequest(BaseModel):
    items: list[RecurringSuggestionAccept] = Field(
        min_length=1, max_length=MAX_SUGGESTION_ITEMS
    )


class RecurringSuggestionAcceptResult(BaseModel):
    fingerprint: str
    status: Literal["created", "error"]
    recurring: RecurringTransactionDetail | None = None
    error: str | None = None


class RecurringSuggestionDismissItem(BaseModel):
    fingerprint: str = Field(
        min_length=FINGERPRINT_LENGTH,
        max_length=FINGERPRINT_LENGTH,
        pattern=FINGERPRINT_PATTERN,
    )
    label: str | None = Field(default=None, max_length=200)


class RecurringSuggestionDismissRequest(BaseModel):
    items: list[RecurringSuggestionDismissItem] = Field(
        min_length=1, max_length=MAX_SUGGESTION_ITEMS
    )


class RecurringSuggestionDismissResult(BaseModel):
    dismissed: int
