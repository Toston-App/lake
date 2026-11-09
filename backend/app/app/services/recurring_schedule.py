"""Schedule bookkeeping of recurring templates: their rule and next occurrence."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.core.recurrence import (
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    Occurrence,
    Rule,
    next_occurrence,
    upcoming,
)

RecurringTransaction = models.RecurringTransaction

UPCOMING_PREVIEW = 5

_postgres_timezones: frozenset[str] | None = None


def local_today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


async def postgres_timezones(db: AsyncSession) -> frozenset[str]:
    """Timezone names the database knows, loaded once per process."""
    global _postgres_timezones
    if _postgres_timezones is None:
        result = await db.execute(text("SELECT name FROM pg_timezone_names"))
        _postgres_timezones = frozenset(result.scalars())
    return _postgres_timezones


def rule_from(source: RecurringTransaction | Mapping) -> Rule:
    """The rule of a template, or of a mapping of template fields."""
    if isinstance(source, Mapping):
        get = source.get
    else:

        def get(field):
            return getattr(source, field)

    return Rule(
        frequency=Frequency(get("frequency")),
        start_date=get("start_date"),
        interval=get("interval") or 1,
        weekdays=tuple(get("weekdays") or ()),
        monthly_mode=MonthlyMode(get("monthly_mode") or MonthlyMode.day_of_month),
        month_days=tuple(get("month_days") or ()),
        week_of_month=get("week_of_month"),
        adjustment=BusinessDayAdjustment(
            get("business_day_adjustment") or BusinessDayAdjustment.none
        ),
        end_date=get("end_date"),
    )


rule_for = rule_from


def _limit_reached(template: RecurringTransaction) -> bool:
    return bool(
        template.max_occurrences
        and template.occurrences_count >= template.max_occurrences
    )


def _set_next(template: RecurringTransaction, occurrence: Occurrence | None) -> None:
    if occurrence is None or _limit_reached(template):
        template.next_nominal_date = None
        template.next_run_date = None
        return
    template.next_nominal_date = occurrence.nominal
    template.next_run_date = occurrence.actual


def _reschedule(
    template: RecurringTransaction, *, from_date: date | None = None
) -> None:
    """Recompute the next occurrence from scratch, after the last generated one."""
    _set_next(
        template,
        next_occurrence(
            rule_from(template),
            last_actual=template.last_run_date,
            from_date=from_date,
            today=local_today(template.timezone),
        ),
    )


def _advance(template: RecurringTransaction) -> None:
    _set_next(
        template,
        next_occurrence(
            rule_from(template),
            after_nominal=template.next_nominal_date,
            last_actual=max(
                d for d in (template.last_run_date, template.next_run_date) if d
            ),
        ),
    )


def upcoming_for(
    template: RecurringTransaction, count: int = UPCOMING_PREVIEW
) -> list[Occurrence]:
    if not template.next_run_date:
        return []
    if template.max_occurrences:
        count = min(count, template.max_occurrences - template.occurrences_count)
    if count <= 0:
        return []
    first = Occurrence(
        nominal=template.next_nominal_date, actual=template.next_run_date
    )
    return [first] + upcoming(
        rule_from(template),
        count - 1,
        after_nominal=first.nominal,
        last_actual=first.actual,
    )


def to_detail(template: RecurringTransaction) -> schemas.RecurringTransactionDetail:
    detail = schemas.RecurringTransactionDetail.model_validate(template)
    detail.upcoming = [
        schemas.UpcomingOccurrence(nominal_date=o.nominal, date=o.actual)
        for o in upcoming_for(template)
    ]
    return detail
