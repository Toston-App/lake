"""Pure date math for recurring transactions.

A rule produces *nominal* dates (the 15th, the last day of the month, every
other Friday...). Each nominal date is then moved off weekends according to
the business-day adjustment, giving the *actual* date the transaction is
recorded on. The series always advances from nominal dates, so moving one
occurrence never shifts the next one.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

LAST_DAY = -1
SATURDAY = 5
# Hard stop for scans over pathological rules (e.g. Feb 30 never matching).
MAX_PERIODS_SCANNED = 5000


class RecurringType(str, Enum):
    expense = "expense"
    income = "income"
    transfer = "transfer"


class Frequency(str, Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    semimonthly = "semimonthly"
    yearly = "yearly"


class MonthlyMode(str, Enum):
    day_of_month = "day_of_month"
    nth_weekday = "nth_weekday"


class BusinessDayAdjustment(str, Enum):
    none = "none"
    previous = "previous"
    next = "next"


@dataclass(frozen=True)
class Rule:
    frequency: Frequency
    start_date: date
    interval: int = 1
    # weekly: days of the week (0=Monday). nth_weekday: a single weekday.
    weekdays: tuple[int, ...] = ()
    monthly_mode: MonthlyMode = MonthlyMode.day_of_month
    # monthly/semimonthly: days of the month, -1 meaning the last day.
    month_days: tuple[int, ...] = ()
    # nth_weekday: 1-4, or -1 for the last one in the month.
    week_of_month: int | None = None
    adjustment: BusinessDayAdjustment = BusinessDayAdjustment.none
    end_date: date | None = None


@dataclass(frozen=True)
class Occurrence:
    nominal: date
    actual: date


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _resolve_month_day(year: int, month: int, day: int) -> date:
    """Day of month, clamped to the month's length (31 -> 30, 28 or 29)."""
    last = _last_day(year, month)
    if day == LAST_DAY or day > last:
        return date(year, month, last)
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    if n == LAST_DAY:
        last = date(year, month, _last_day(year, month))
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(year, month, 1)
    day = first + timedelta(days=(weekday - first.weekday()) % 7)
    return day + timedelta(weeks=n - 1)


def _add_months(year: int, month: int, months: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def adjust_for_weekend(day: date, adjustment: BusinessDayAdjustment) -> date:
    if day.weekday() < SATURDAY or adjustment == BusinessDayAdjustment.none:
        return day
    if adjustment == BusinessDayAdjustment.previous:
        return day - timedelta(days=day.weekday() - 4)
    return day + timedelta(days=7 - day.weekday())


def _month_days(rule: Rule) -> tuple[int, ...]:
    if rule.month_days:
        return rule.month_days
    if rule.frequency == Frequency.semimonthly:
        return (15, LAST_DAY)
    return (rule.start_date.day,)


def _period_dates(rule: Rule, k: int) -> list[date]:
    """Nominal dates of the k-th period after the rule's start, unsorted."""
    start = rule.start_date
    step = rule.interval * k
    if rule.frequency == Frequency.daily:
        return [start + timedelta(days=step)]
    if rule.frequency == Frequency.weekly:
        week_start = start - timedelta(days=start.weekday()) + timedelta(weeks=step)
        weekdays = rule.weekdays or (start.weekday(),)
        return [week_start + timedelta(days=d) for d in weekdays]
    if rule.frequency in (Frequency.monthly, Frequency.semimonthly):
        year, month = _add_months(start.year, start.month, step)
        if (
            rule.frequency == Frequency.monthly
            and rule.monthly_mode == MonthlyMode.nth_weekday
        ):
            weekday = rule.weekdays[0] if rule.weekdays else start.weekday()
            n = rule.week_of_month or 1
            return [_nth_weekday(year, month, weekday, n)]
        return [_resolve_month_day(year, month, d) for d in _month_days(rule)]
    # yearly: Feb 29 falls back to Feb 28 in non-leap years.
    year = start.year + step
    return [_resolve_month_day(year, start.month, start.day)]


def _first_period(rule: Rule, from_date: date) -> int:
    start = rule.start_date
    if from_date <= start:
        return 0
    if rule.frequency == Frequency.daily:
        return (from_date - start).days // rule.interval
    if rule.frequency == Frequency.weekly:
        week_start = start - timedelta(days=start.weekday())
        return (from_date - week_start).days // 7 // rule.interval
    if rule.frequency in (Frequency.monthly, Frequency.semimonthly):
        months = (from_date.year - start.year) * 12 + from_date.month - start.month
        return months // rule.interval
    return (from_date.year - start.year) // rule.interval


def iter_nominal(rule: Rule, from_date: date | None = None) -> Iterator[date]:
    """Yield nominal dates >= max(from_date, start_date), ascending, until end_date."""
    lower = max(from_date or rule.start_date, rule.start_date)
    k = _first_period(rule, lower)
    for _ in range(MAX_PERIODS_SCANNED):
        for day in sorted(set(_period_dates(rule, k))):
            if day < lower:
                continue
            if rule.end_date and day > rule.end_date:
                return
            yield day
        k += 1


def next_occurrence(
    rule: Rule,
    *,
    after_nominal: date | None = None,
    last_actual: date | None = None,
    from_date: date | None = None,
) -> Occurrence | None:
    """First occurrence after `after_nominal` whose actual date is after `last_actual`.

    The actual date is never before the rule's start date, and two nominal
    dates that land on the same actual date only produce one occurrence.
    """
    lower = from_date
    if after_nominal is not None:
        candidate = after_nominal + timedelta(days=1)
        lower = max(lower, candidate) if lower else candidate
    elif last_actual is not None and lower is None:
        # A weekend adjustment moves dates by at most 2 days, so a nominal
        # date a few days before the last actual one may still be pending.
        lower = last_actual - timedelta(days=3)
    for nominal in iter_nominal(rule, lower):
        actual = max(adjust_for_weekend(nominal, rule.adjustment), rule.start_date)
        if last_actual is not None and actual <= last_actual:
            continue
        return Occurrence(nominal=nominal, actual=actual)
    return None


def upcoming(
    rule: Rule,
    count: int,
    *,
    after_nominal: date | None = None,
    last_actual: date | None = None,
    from_date: date | None = None,
) -> list[Occurrence]:
    out: list[Occurrence] = []
    occurrence = next_occurrence(
        rule, after_nominal=after_nominal, last_actual=last_actual, from_date=from_date
    )
    while occurrence and len(out) < count:
        out.append(occurrence)
        occurrence = next_occurrence(
            rule, after_nominal=occurrence.nominal, last_actual=occurrence.actual
        )
    return out
