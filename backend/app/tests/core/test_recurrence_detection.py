"""Pure recurring-series detection: normalization, schedules, amounts, scoring."""

from datetime import date, timedelta

from app.core.recurrence import (
    BusinessDayAdjustment,
    Frequency,
    RecurringType,
    adjust_for_weekend,
)
from app.core.recurrence_detection import (
    Merchant,
    TxPoint,
    detect_series,
    normalize_description,
    point_key,
)

TODAY = date(2026, 11, 3)
_ids = iter(range(1, 100_000))


def _point(day: date, amount: float = 10.0, **kw) -> TxPoint:
    return TxPoint(
        id=next(_ids),
        type=kw.pop("type", RecurringType.expense),
        date=day,
        amount=amount,
        description=kw.pop("description", "netflix"),
        account_id=kw.pop("account_id", 1),
        **kw,
    )


def _detect(points: list[TxPoint], key: str = "netflix") -> list:
    merchants = {point_key(p): Merchant(key, key.title()) for p in points}
    return detect_series(points, merchants, TODAY)


def _monthly(day: int, months: int, end=(2026, 10)):
    year, month = end
    out = []
    for _ in range(months):
        out.append(date(year, month, day))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return sorted(out)


# -- normalize ---------------------------------------------------------------
def test_normalize_removes_card_fragments_dates_and_references():
    assert normalize_description("NETFLIX.COM *1234 12/10 REF ABC12345") == "netflix"
    assert normalize_description("Pago tarjeta Spotify xxxx9876") == "spotify"


def test_normalize_removes_accents_domains_and_noise():
    assert normalize_description("Compra en línea www.amazon.com.mx") == "amazon"
    assert normalize_description("Café   Ñandú") == "cafe nandu"


def test_normalize_empty():
    assert normalize_description(None) == ""
    assert normalize_description("12345 *9999") == ""


# -- schedules ---------------------------------------------------------------
def test_monthly_on_the_15th_with_weekend_dates_moved_to_friday():
    dates = [
        adjust_for_weekend(d, BusinessDayAdjustment.previous) for d in _monthly(15, 12)
    ]
    [found] = _detect([_point(d) for d in dates])
    assert found.detected.frequency == Frequency.monthly
    assert found.detected.month_days == (15,)
    assert found.detected.adjustment == BusinessDayAdjustment.previous
    assert found.next_expected_date == adjust_for_weekend(
        date(2026, 11, 15), BusinessDayAdjustment.previous
    )


def test_monthly_at_month_end():
    dates = []
    for month in range(11, 23):
        year, m = 2025 + (month - 1) // 12, (month - 1) % 12 + 1
        last = date(year + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        dates.append(last)
    dates = [d for d in dates if d <= TODAY]
    [found] = _detect([_point(d) for d in dates])
    assert found.detected.frequency == Frequency.monthly
    assert found.detected.month_days == (-1,)


def test_weekly_and_every_two_weeks():
    start = date(2026, 5, 4)  # a Monday
    weekly = [_point(start + timedelta(weeks=i)) for i in range(26)]
    [found] = _detect(weekly)
    assert (found.detected.frequency, found.detected.interval) == (Frequency.weekly, 1)
    assert found.detected.weekdays == (0,)

    biweekly = [
        _point(start + timedelta(weeks=2 * i), description="gym") for i in range(13)
    ]
    [found] = _detect(biweekly, key="gym")
    assert (found.detected.frequency, found.detected.interval) == (Frequency.weekly, 2)


def test_semimonthly_on_1st_and_15th():
    dates = sorted(_monthly(1, 6) + _monthly(15, 6))
    [found] = _detect([_point(d, type=RecurringType.income) for d in dates], "salary")
    assert found.detected.frequency == Frequency.semimonthly
    assert found.detected.month_days == (1, 15)


def test_yearly_with_two_points_over_two_years():
    points = [_point(date(2025, 3, 10), 99), _point(date(2026, 3, 12), 99)]
    [found] = _detect(points, "domain")
    assert found.detected.frequency == Frequency.yearly


# -- amounts -----------------------------------------------------------------
def test_price_change_keeps_one_series_with_a_reason():
    dates = _monthly(15, 12)
    points = [_point(d, 9.99 if d < date(2026, 3, 1) else 11.99) for d in dates]
    [found] = _detect(points)
    assert len(found.points) == 12
    assert "price changed in Mar 2026" in found.reasons


def test_two_subscriptions_from_one_merchant_at_different_amounts():
    points = [_point(d, 9.99) for d in _monthly(5, 10)]
    points += [_point(d, 2.99) for d in _monthly(20, 10)]
    found = _detect(points, "apple")
    assert len(found) == 2
    assert len({c.fingerprint for c in found}) == 2


def test_one_missed_month_still_detected_with_lower_confidence():
    full = _detect([_point(d) for d in _monthly(15, 12)])[0]
    dates = [d for d in _monthly(15, 12) if d != date(2026, 6, 15)]
    [missed] = _detect([_point(d) for d in dates])
    assert missed.detected.frequency == Frequency.monthly
    assert missed.confidence < full.confidence


def test_stopped_subscription_is_dropped():
    dates = _monthly(15, 8, end=(2026, 6))
    assert _detect([_point(d) for d in dates]) == []


def test_irregular_spending_is_not_detected():
    gaps = [3, 11, 2, 25, 7, 1, 19, 4, 40, 9, 6]
    day = date(2026, 1, 2)
    points = []
    for gap in gaps:
        day += timedelta(days=gap)
        points.append(_point(day, 30))
    assert _detect(points, "oxxo") == []


def test_same_day_duplicates_do_not_break_the_gaps():
    dates = _monthly(15, 6)
    points = [_point(d) for d in dates] + [_point(dates[2]), _point(dates[4])]
    [found] = _detect(points)
    assert len(found.points) == 6


def test_no_text_group_needs_exact_amounts():
    points = [
        _point(d, 50 + i, description=None, category_id=3)
        for i, d in enumerate(_monthly(15, 6))
    ]
    assert detect_series(points, {}, TODAY) == []
    exact = [_point(d, 50, description=None, category_id=3) for d in _monthly(15, 6)]
    [found] = detect_series(exact, {}, TODAY)
    assert not found.has_text


# -- fingerprint -------------------------------------------------------------
def test_fingerprint_is_stable_across_price_changes_and_differs_by_frequency():
    before = _detect([_point(d, 9.99) for d in _monthly(15, 6)])[0]
    after = _detect([_point(d, 12.99) for d in _monthly(15, 6)])[0]
    assert before.fingerprint == after.fingerprint

    start = date(2026, 5, 4)
    weekly = _detect([_point(start + timedelta(weeks=i)) for i in range(26)])[0]
    assert weekly.fingerprint != before.fingerprint


# -- month end ---------------------------------------------------------------
def test_rent_on_the_28th_moved_to_friday_keeps_the_28th():
    dates = _monthly(28, 12)
    # One month the 28th fell on a weekend and the rent came on Friday the 27th.
    dates = [adjust_for_weekend(d, BusinessDayAdjustment.previous) for d in dates]
    [found] = _detect([_point(d, 900) for d in dates], "rent")
    assert found.detected.month_days == (28,)


def test_rent_on_the_30th_is_not_month_end():
    dates = []
    for year, month in [
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
        (2026, 3),
        (2026, 4),
        (2026, 5),
        (2026, 6),
        (2026, 7),
        (2026, 8),
        (2026, 9),
        (2026, 10),
    ]:
        dates.append(date(year, month, 28 if month == 2 else 30))
    [found] = _detect([_point(d, 900) for d in dates], "rent")
    assert found.detected.month_days == (30,)


# -- types and ids -----------------------------------------------------------
def test_points_of_different_types_can_share_ids():
    netflix = [_point(d, 15.99) for d in _monthly(10, 6)]
    gifts = [
        TxPoint(
            id=p.id,
            type=RecurringType.income,
            date=p.date - timedelta(days=700),
            amount=50,
            description="gift",
        )
        for p in netflix
    ]
    merchants = {point_key(p): Merchant("netflix", "Netflix") for p in netflix}
    merchants.update({point_key(p): Merchant("gift", "Gift") for p in gifts})
    [found] = detect_series(netflix + gifts, merchants, TODAY)
    assert found.merchant_name == "Netflix"
    assert len(found.points) == 6


def test_next_expected_date_skips_missed_dates():
    # Last charge on Sep 24; Oct 24 was missed (today is Nov 3).
    dates = _monthly(24, 6, end=(2026, 9))
    [found] = _detect([_point(d) for d in dates])
    assert found.next_expected_date == date(2026, 11, 24)
    assert "nothing recorded on 2026-10-24" in found.reasons
