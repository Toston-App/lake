from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.recurrence import (
    LAST_DAY,
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    RecurringType,
)
from app.schemas.transaction import TransactionType

MAX_INTERVAL = 365


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
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown timezone") from exc
        return v


class RecurringTransactionFields(RecurrenceRule):
    description: str | None = None
    amount: float | None = Field(default=None, gt=0)
    account_id: int | None = None
    to_account_id: int | None = None
    category_id: int | None = None
    subcategory_id: int | None = None
    place_id: int | None = None
    start_date: date | None = None

    @field_validator("amount")
    @classmethod
    def round_amount(cls, v: float | None) -> float | None:
        return round(v, 2) if v is not None else v


class RecurringTransactionCreate(RecurringTransactionFields):
    type: RecurringType
    amount: float = Field(gt=0)
    frequency: Frequency
    start_date: date

    @model_validator(mode="after")
    def validate_accounts(self) -> "RecurringTransactionCreate":
        if self.type == RecurringType.transfer:
            if not self.account_id or not self.to_account_id:
                raise ValueError("Transfers need account_id and to_account_id")
        return self


class RecurringTransactionUpdate(RecurringTransactionFields):
    is_active: bool | None = None


class RecurringFromTransaction(RecurrenceRule):
    """Turn an existing transaction into a recurring template."""

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
    type: RecurringType
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
