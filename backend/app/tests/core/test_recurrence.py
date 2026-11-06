"""Date math for recurring transaction rules."""

import ast
from datetime import date
from pathlib import Path

import pytest
from app.core.recurrence import (
    LAST_DAY,
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    Occurrence,
    Rule,
    add_months,
    adjust_for_weekend,
    last_day,
    next_occurrence,
    resolve_month_day,
    snap_to_nominal,
    upcoming,
)

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _actual(rule: Rule, count: int, **kwargs) -> list[date]:
    return [o.actual for o in upcoming(rule, count, **kwargs)]


def test_daily_every_three_days():
    rule = Rule(Frequency.daily, date(2025, 1, 1), interval=3)
    assert _actual(rule, 3) == [date(2025, 1, 1), date(2025, 1, 4), date(2025, 1, 7)]


def test_weekly_defaults_to_start_weekday_every_two_weeks():
    rule = Rule(Frequency.weekly, date(2025, 1, 3), interval=2)  # Friday
    assert _actual(rule, 3) == [date(2025, 1, 3), date(2025, 1, 17), date(2025, 1, 31)]


def test_weekly_multiple_weekdays_skips_days_before_start():
    rule = Rule(Frequency.weekly, date(2025, 1, 2), weekdays=(0, 3))  # Mon, Thu
    assert _actual(rule, 3) == [date(2025, 1, 2), date(2025, 1, 6), date(2025, 1, 9)]


def test_monthly_day_31_clamps_and_keeps_anchor():
    rule = Rule(Frequency.monthly, date(2025, 1, 31))
    assert _actual(rule, 4) == [
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
    ]


def test_monthly_last_day_handles_leap_year():
    rule = Rule(Frequency.monthly, date(2024, 1, 10), month_days=(-1,))
    assert _actual(rule, 3) == [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31)]


def test_monthly_nth_weekday():
    first_monday = Rule(
        Frequency.monthly,
        date(2025, 1, 1),
        monthly_mode=MonthlyMode.nth_weekday,
        weekdays=(0,),
        week_of_month=1,
    )
    assert _actual(first_monday, 2) == [date(2025, 1, 6), date(2025, 2, 3)]
    last_friday = Rule(
        Frequency.monthly,
        date(2025, 1, 1),
        monthly_mode=MonthlyMode.nth_weekday,
        weekdays=(4,),
        week_of_month=-1,
    )
    assert _actual(last_friday, 2) == [date(2025, 1, 31), date(2025, 2, 28)]


def test_semimonthly_defaults_to_15_and_last_day():
    rule = Rule(Frequency.semimonthly, date(2025, 1, 1))
    assert _actual(rule, 4) == [
        date(2025, 1, 15),
        date(2025, 1, 31),
        date(2025, 2, 15),
        date(2025, 2, 28),
    ]


def test_quincena_moves_weekend_paydays_to_friday_without_drift():
    rule = Rule(
        Frequency.semimonthly,
        date(2025, 2, 1),
        adjustment=BusinessDayAdjustment.previous,
    )
    occurrences = upcoming(rule, 4)
    # Feb 15 2025 is a Saturday -> Friday 14; Mar 15 is a Saturday -> 14 too.
    assert [(o.nominal, o.actual) for o in occurrences] == [
        (date(2025, 2, 15), date(2025, 2, 14)),
        (date(2025, 2, 28), date(2025, 2, 28)),
        (date(2025, 3, 15), date(2025, 3, 14)),
        (date(2025, 3, 31), date(2025, 3, 31)),
    ]


def test_last_day_on_weekend_moves_to_next_monday_across_month():
    rule = Rule(
        Frequency.monthly,
        date(2025, 5, 1),
        month_days=(-1,),
        adjustment=BusinessDayAdjustment.next,
    )
    # May 31 2025 is a Saturday.
    assert _actual(rule, 1) == [date(2025, 6, 2)]


def test_yearly_feb_29_falls_back_to_feb_28():
    rule = Rule(Frequency.yearly, date(2024, 2, 29))
    assert _actual(rule, 3) == [date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28)]


def test_end_date_stops_series():
    rule = Rule(Frequency.monthly, date(2025, 1, 5), end_date=date(2025, 3, 5))
    assert _actual(rule, 10) == [date(2025, 1, 5), date(2025, 2, 5), date(2025, 3, 5)]


def test_weekend_start_moves_to_previous_friday():
    rule = Rule(
        Frequency.monthly,
        date(2025, 3, 15),
        adjustment=BusinessDayAdjustment.previous,
    )
    assert _actual(rule, 1) == [date(2025, 3, 14)]
    # Set up on Thursday: Friday is still ahead.
    occurrence = next_occurrence(rule, today=date(2025, 3, 13))
    assert occurrence.actual == date(2025, 3, 14)


def test_weekend_start_stays_when_previous_friday_has_come():
    rule = Rule(
        Frequency.semimonthly,
        date(2025, 3, 15),
        adjustment=BusinessDayAdjustment.previous,
    )
    # Set up on Friday 14 (or later) for a series starting Saturday 15.
    for today in (date(2025, 3, 14), date(2025, 3, 15)):
        assert upcoming(rule, 2, today=today) == [
            Occurrence(nominal=date(2025, 3, 15), actual=date(2025, 3, 15)),
            Occurrence(nominal=date(2025, 3, 31), actual=date(2025, 3, 31)),
        ]


def test_collapsed_dates_produce_single_occurrence():
    # Sat 30 and Sun 31 Aug 2025 both move to Friday 29.
    rule = Rule(
        Frequency.monthly,
        date(2025, 8, 1),
        month_days=(29, 30, -1),
        adjustment=BusinessDayAdjustment.previous,
    )
    assert _actual(rule, 2) == [date(2025, 8, 29), date(2025, 9, 29)]


def test_next_occurrence_after_last_actual_skips_already_recorded():
    # The 15 Mar paycheck was recorded on Friday 14; it must not repeat.
    rule = Rule(
        Frequency.semimonthly,
        date(2025, 3, 14),
        adjustment=BusinessDayAdjustment.previous,
    )
    occurrence = next_occurrence(rule, last_actual=date(2025, 3, 14))
    assert occurrence is not None
    assert occurrence.actual == date(2025, 3, 31)


def test_from_date_skips_missed_periods():
    rule = Rule(Frequency.monthly, date(2025, 1, 10))
    occurrence = next_occurrence(rule, from_date=date(2025, 6, 11))
    assert occurrence is not None
    assert occurrence.actual == date(2025, 7, 10)


def test_adjust_for_weekend():
    saturday = date(2025, 2, 15)
    sunday = date(2025, 2, 16)
    assert adjust_for_weekend(saturday, BusinessDayAdjustment.none) == saturday
    assert adjust_for_weekend(saturday, BusinessDayAdjustment.previous) == date(
        2025, 2, 14
    )
    assert adjust_for_weekend(sunday, BusinessDayAdjustment.previous) == date(
        2025, 2, 14
    )
    assert adjust_for_weekend(saturday, BusinessDayAdjustment.next) == date(2025, 2, 17)
    assert adjust_for_weekend(sunday, BusinessDayAdjustment.next) == date(2025, 2, 17)


def test_series_stops_at_the_last_representable_date():
    # Past year 9999 there are no more dates: the series ends instead of raising.
    for frequency in (Frequency.monthly, Frequency.yearly):
        rule = Rule(frequency, date(9999, 12, 20))
        assert _actual(rule, 5) == [date(9999, 12, 20)]
    weekly = Rule(Frequency.weekly, date(9999, 12, 20))
    assert _actual(weekly, 5) == [date(9999, 12, 20), date(9999, 12, 27)]
    daily = Rule(Frequency.daily, date(9999, 12, 30))
    assert _actual(daily, 5) == [date(9999, 12, 30), date(9999, 12, 31)]
    assert next_occurrence(daily, after_nominal=date(9999, 12, 31)) is None


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2024, 2, 29),
        (2025, 2, 28),
        (2100, 2, 28),
        (2000, 2, 29),
        (2025, 4, 30),
        (2025, 12, 31),
    ],
)
def test_last_day(year, month, expected):
    assert last_day(year, month) == expected


@pytest.mark.parametrize(
    ("year", "month", "day", "expected"),
    [
        (2025, 4, 31, date(2025, 4, 30)),  # day 31 in a 30-day month
        (2025, 2, 31, date(2025, 2, 28)),
        (2024, 2, 30, date(2024, 2, 29)),  # leap year keeps Feb 29
        (2025, 2, 29, date(2025, 2, 28)),  # Feb 29 outside a leap year
        (2024, 2, LAST_DAY, date(2024, 2, 29)),
        (2025, 6, LAST_DAY, date(2025, 6, 30)),
        (2025, 7, LAST_DAY, date(2025, 7, 31)),
        (2025, 6, 15, date(2025, 6, 15)),
    ],
)
def test_resolve_month_day_clamps_to_month_length(year, month, day, expected):
    assert resolve_month_day(year, month, day) == expected


@pytest.mark.parametrize(
    ("year", "month", "months", "expected"),
    [
        (2025, 1, 1, (2025, 2)),
        (2025, 12, 1, (2026, 1)),
        (2025, 1, -1, (2024, 12)),
        (2025, 6, 0, (2025, 6)),
        (2025, 3, 25, (2027, 4)),
        (2025, 3, -27, (2022, 12)),
    ],
)
def test_add_months_wraps_years(year, month, months, expected):
    assert add_months(year, month, months) == expected


def test_snap_to_nominal_finds_the_date_a_charge_was_due():
    month_end = Rule(Frequency.monthly, date(2025, 1, 1), month_days=(LAST_DAY,))
    # Charged on Friday for a Sunday month end.
    assert snap_to_nominal(month_end, date(2025, 8, 29)) == date(2025, 8, 31)
    # Charged the Monday after a Saturday month end, across the month boundary.
    assert snap_to_nominal(month_end, date(2025, 6, 2)) == date(2025, 5, 31)
    # Exactly three days off still snaps; four days off does not.
    on_15 = Rule(Frequency.monthly, date(2025, 1, 15))
    assert snap_to_nominal(on_15, date(2025, 3, 18)) == date(2025, 3, 15)
    assert snap_to_nominal(on_15, date(2025, 3, 19)) is None
    # Day 31 in a 30-day month resolves to the 30th.
    on_31 = Rule(Frequency.monthly, date(2025, 1, 31))
    assert snap_to_nominal(on_31, date(2025, 4, 30)) == date(2025, 4, 30)
    # Feb 29 yearly falls back to Feb 28 in non-leap years.
    leap = Rule(Frequency.yearly, date(2024, 2, 29))
    assert snap_to_nominal(leap, date(2025, 3, 1)) == date(2025, 2, 28)
    assert snap_to_nominal(leap, date(2028, 3, 1)) == date(2028, 2, 29)
    # Before the rule starts there is nothing to snap to.
    assert snap_to_nominal(on_15, date(2024, 12, 1)) is None


def _imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


@pytest.mark.parametrize("module", ["core/recurrence.py", "services/recurring.py"])
def test_scheduling_does_not_depend_on_detection(module):
    imported = _imported_modules(APP_DIR / module)
    assert not any(m.startswith("app.core.recurrence_detection") for m in imported)
