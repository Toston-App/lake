"""Date math for recurring transaction rules."""

from datetime import date

from app.core.recurrence import (
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    Rule,
    adjust_for_weekend,
    next_occurrence,
    upcoming,
)


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


def test_actual_date_never_before_start():
    # Saturday start with "previous" would land on Friday, before the start.
    rule = Rule(
        Frequency.monthly,
        date(2025, 3, 15),
        adjustment=BusinessDayAdjustment.previous,
    )
    assert _actual(rule, 1) == [date(2025, 3, 15)]


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
