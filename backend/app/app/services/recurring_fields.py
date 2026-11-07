"""Recurring template fields: their validation, and the entities they reference."""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.core.recurrence import (
    Frequency,
    MonthlyMode,
    Rule,
    next_occurrence,
    upcoming,
)
from app.schemas.transaction import TransactionType
from app.services.errors import DomainError
from app.services.ownership import require_owned
from app.services.recurring_schedule import (
    UPCOMING_PREVIEW,
    RecurringTransaction,
    local_today,
    postgres_timezones,
    rule_from,
)

# A series may start at most this far back: it back-fills from its start date.
MAX_PAST_YEARS = 2
# Start and end dates further ahead than this are rejected.
MAX_FUTURE_YEARS = 10


class InvalidTimezoneError(DomainError):
    # Same status as a timezone the request schema rejects.
    status_code = 422


async def _validate_timezone(db: AsyncSession, tz: str) -> None:
    # Python's tzdata may know zones the database doesn't, and the worker's
    # due query evaluates every template's timezone in SQL.
    if tz not in await postgres_timezones(db):
        raise InvalidTimezoneError("Unknown timezone")


def _shift_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # Feb 29 in a non-leap year
        return day.replace(year=day.year + years, day=28)


def earliest_start(today: date) -> date:
    return _shift_years(today, -MAX_PAST_YEARS)


def _validate_dates(fields: dict, keys=("start_date", "end_date")) -> None:
    """Keep start and end dates within MAX_PAST_YEARS / MAX_FUTURE_YEARS of today."""
    today = local_today(fields.get("timezone") or "UTC")
    latest = _shift_years(today, MAX_FUTURE_YEARS)
    start = fields.get("start_date")
    if "start_date" in keys and start is not None:
        if start < earliest_start(today):
            raise DomainError(
                f"start_date can't be more than {MAX_PAST_YEARS} years in the past"
            )
        if start > latest:
            raise DomainError(
                f"start_date can't be more than {MAX_FUTURE_YEARS} years ahead"
            )
    end = fields.get("end_date")
    if "end_date" in keys and end is not None and end > latest:
        raise DomainError(f"end_date can't be more than {MAX_FUTURE_YEARS} years ahead")


def _validate_rule(rule: Rule) -> None:
    if rule.end_date and rule.end_date < rule.start_date:
        raise DomainError("end_date must be on or after start_date")
    if rule.frequency == Frequency.semimonthly and rule.month_days:
        if len(rule.month_days) != 2:
            raise DomainError("Twice-a-month schedules need exactly two month_days")
    if (
        rule.frequency == Frequency.monthly
        and rule.monthly_mode == MonthlyMode.nth_weekday
    ):
        if rule.week_of_month is None:
            raise DomainError("nth_weekday schedules need week_of_month")
        if len(rule.weekdays) > 1:
            raise DomainError("nth_weekday schedules take a single weekday")
    if next_occurrence(rule) is None:
        raise DomainError("This schedule never produces a date")


def preview(data: schemas.RecurringPreviewRequest) -> schemas.RecurringPreview:
    fields = data.model_dump()
    _validate_dates(fields)
    rule = rule_from(fields)
    _validate_rule(rule)
    count = min(UPCOMING_PREVIEW, data.max_occurrences or UPCOMING_PREVIEW)
    after = data.start_date if data.exclude_start else None
    if after and data.max_occurrences:
        count = min(count, data.max_occurrences - 1)
    return schemas.RecurringPreview(
        upcoming=[
            schemas.UpcomingOccurrence(nominal_date=o.nominal, date=o.actual)
            for o in upcoming(
                rule,
                count,
                after_nominal=after,
                last_actual=after,
                today=local_today(data.timezone or "UTC"),
            )
        ]
    )


# Template reference fields: what they point at, and its name in errors.
_REFERENCES = {
    "account_id": (crud.account, "Account"),
    "to_account_id": (crud.account, "Account"),
    "category_id": (crud.category, "Category"),
    "subcategory_id": (crud.subcategory, "Subcategory"),
    "place_id": (crud.place, "Place"),
}


async def _check_reference(
    db: AsyncSession, user: models.User, field: str, obj_id: int | None
):
    """A template's reference; another user's entity is reported as missing."""
    crud_obj, label = _REFERENCES[field]
    return await require_owned(db, user, crud_obj, obj_id, label, hide_foreign=True)


async def _validate_fields(
    db: AsyncSession, user: models.User, recurring_type: TransactionType, fields: dict
) -> dict:
    """Check ownership and per-type rules; drop fields that don't apply to the type."""
    await _check_reference(db, user, "account_id", fields.get("account_id"))
    if recurring_type == TransactionType.transfer:
        await _check_reference(db, user, "to_account_id", fields.get("to_account_id"))
        if not fields.get("account_id") or not fields.get("to_account_id"):
            raise DomainError("Transfers need account_id and to_account_id")
        if fields["account_id"] == fields["to_account_id"]:
            raise DomainError("Transfers need two different accounts")
        fields.update(category_id=None, subcategory_id=None, place_id=None)
        return fields

    fields["to_account_id"] = None
    await _check_reference(db, user, "place_id", fields.get("place_id"))
    subcategory = await _check_reference(
        db, user, "subcategory_id", fields.get("subcategory_id")
    )
    if recurring_type == TransactionType.income:
        # Incomes are categorised through their subcategory only.
        fields["category_id"] = None
        return fields

    await _check_reference(db, user, "category_id", fields.get("category_id"))
    if subcategory and subcategory.category_id != fields.get("category_id"):
        raise DomainError("Subcategory does not belong to the category")
    return fields


# Template columns that point at each kind of referenced entity.
_REFERENCE_COLUMNS = {
    "account": ("account_id", "to_account_id"),
    "category": ("category_id",),
    "subcategory": ("subcategory_id",),
    "place": ("place_id",),
}


async def pause_for_deleted(db: AsyncSession, kind: str, ids: list[int]) -> None:
    """Pause the templates that use an entity which is about to be deleted.

    The template foreign keys are ON DELETE SET NULL, so once the entity is gone
    a template that lost it looks like one that never had it and would keep
    generating unassigned transactions. Call this before the delete, in the same
    transaction.
    """
    if not ids:
        return
    columns = [getattr(RecurringTransaction, c) for c in _REFERENCE_COLUMNS[kind]]
    await db.execute(
        update(RecurringTransaction)
        .where(or_(*(column.in_(ids) for column in columns)))
        .values(
            is_active=False,
            last_error=f"The {kind} used by this recurring transaction was deleted",
        )
        .execution_options(synchronize_session=False)
    )
