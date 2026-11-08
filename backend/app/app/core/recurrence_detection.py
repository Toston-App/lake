"""Pure detection of recurring series in a user's transaction history.

The service loads transactions and resolves each description to a merchant;
this module groups them, splits each group by amount, finds a schedule that
fits the dates and scores the result (`app.core.recurrence_scoring`; text
normalization lives in `app.core.merchant_text`). Nothing here touches the
database or the network, so every threshold can be tested with plain dates.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

# `slugify`, `text_key` and `apply_classification` are re-exported for
# `app.services.recurring_detection`, which imports them from here.
from app.core.merchant_text import slugify as slugify
from app.core.merchant_text import text_key as text_key
from app.core.recurrence import (
    DAY_TOLERANCE,
    LAST_DAY,
    BusinessDayAdjustment,
    Frequency,
    add_months,
    adjust_for_weekend,
    last_day,
    resolve_month_day,
)
from app.core.recurrence_scoring import apply_classification as apply_classification
from app.core.recurrence_scoring import (
    assign_fingerprints,
    base_confidence,
    next_expected,
    series_reasons,
)
from app.core.recurrence_types import (
    STALE_PERIODS,
    Candidate,
    DetectedRule,
    Merchant,
    PointKey,
    TxPoint,
    point_key,
)
from app.core.transaction_type import TransactionType

# Windows: yearly series need two years of history, the rest one year.
WINDOW_DAYS = 395  # ~13 months
YEARLY_WINDOW_DAYS = 760  # ~25 months

MIN_POINTS = 3
MIN_POINTS_YEARLY = 2
MIN_FIT = 0.75
AMOUNT_TOLERANCE = 0.10
# Groups without any text (no description, no place) only match exact amounts.
EXACT_AMOUNT_TOLERANCE = 0.01
MAX_SUGGESTIONS = 50


# ---------------------------------------------------------------------------
# Grouping and amount clustering
# ---------------------------------------------------------------------------
def group_key(point: TxPoint, merchant: Merchant | None) -> tuple:
    if point.type == TransactionType.transfer:
        return (
            point.type,
            point.account_id,
            point.to_account_id,
            merchant.key if merchant else None,
        )
    if merchant is None:
        return (
            point.type,
            None,
            point.account_id,
            point.category_id,
            point.subcategory_id,
        )
    return (point.type, merchant.key)


def dedupe_same_day(points: list[TxPoint]) -> list[TxPoint]:
    """Keep one of each (date, amount): a duplicated import breaks the gaps."""
    seen: set[tuple[date, float]] = set()
    out = []
    for point in sorted(points, key=lambda p: (p.date, p.id)):
        key = (point.date, round(point.amount, 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(point)
    return out


def _within(amount: float, reference: float, tolerance: float, exact: bool) -> bool:
    if exact:
        return abs(amount - reference) <= tolerance
    return abs(amount - reference) <= tolerance * abs(reference)


def cluster_by_amount(
    points: list[TxPoint], *, exact: bool = False
) -> list[list[TxPoint]]:
    """Greedy chronological clustering on the rolling median of the last 3."""
    tolerance = EXACT_AMOUNT_TOLERANCE if exact else AMOUNT_TOLERANCE
    clusters: list[list[TxPoint]] = []
    for point in sorted(points, key=lambda p: (p.date, p.id)):
        best: list[TxPoint] | None = None
        best_diff = None
        for cluster in clusters:
            reference = statistics.median(p.amount for p in cluster[-3:])
            if not _within(point.amount, reference, tolerance, exact):
                continue
            diff = abs(point.amount - reference)
            if best_diff is None or diff < best_diff:
                best, best_diff = cluster, diff
        if best is None:
            clusters.append([point])
        else:
            best.append(point)
    return clusters


# ---------------------------------------------------------------------------
# Frequency detection
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _GapSpec:
    frequency: Frequency
    interval: int
    low: int
    high: int
    period_days: float  # days between occurrences, interval included


GAP_SPECS = (
    _GapSpec(Frequency.weekly, 1, 6, 8, 7),
    _GapSpec(Frequency.weekly, 2, 12, 16, 14),
    _GapSpec(Frequency.semimonthly, 1, 10, 20, 365.25 / 24),
    _GapSpec(Frequency.monthly, 1, 26, 35, 365.25 / 12),
    _GapSpec(Frequency.yearly, 1, 350, 380, 365.25),
)


def _gaps(dates: list[date]) -> list[int]:
    return [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]


def _fit(gaps: list[int], spec: _GapSpec) -> float:
    """Share of gaps that match; a double gap (one missed period) counts half."""
    if not gaps:
        return 0.0
    score = 0.0
    for gap in gaps:
        if spec.low <= gap <= spec.high:
            score += 1
        elif 2 * spec.low <= gap <= 2 * spec.high:
            score += 0.5
    return score / len(gaps)


def _share(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _day_distance(day: date, month_day: int) -> int:
    """Days to the nearest `month_day`, looking at the adjacent months too."""
    distances = []
    for offset in (-1, 0, 1):
        nominal = resolve_month_day(*add_months(day.year, day.month, offset), month_day)
        distances.append(abs((day - nominal).days))
    return min(distances)


def _is_month_end(day: date) -> bool:
    """The month's last day, or the Friday before a month end on a weekend."""
    last = date(day.year, day.month, last_day(day.year, day.month))
    return day == last or day == adjust_for_weekend(
        last, BusinessDayAdjustment.previous
    )


def _month_day_mode(dates: list[date]) -> int | None:
    """The day of month the dates track, -1 for month end, or None.

    Month end needs the dates to follow each month's length (30th in April,
    31st in May); rent on the 28th or 30th keeps its own day.
    """
    if not dates:
        return None
    mode = Counter(d.day for d in dates).most_common(1)[0][0]
    month_end = [_is_month_end(d) for d in dates]
    lengths = {last_day(d.year, d.month) for d in dates}
    if mode >= 28 and _share(month_end) >= MIN_FIT and (len(lengths) > 1 or mode == 31):
        return LAST_DAY
    if _share([_day_distance(d, mode) <= DAY_TOLERANCE for d in dates]) >= MIN_FIT:
        return mode
    return None


def _adjustment(
    dates: list[date], month_days: tuple[int, ...]
) -> BusinessDayAdjustment:
    """Whether charges due on a weekend land on the Friday before or the Monday after."""
    previous = following = weekend = 0
    for day in dates:
        nominal = min(
            (resolve_month_day(day.year, day.month, d) for d in month_days),
            key=lambda n: abs((n - day).days),
        )
        if nominal.weekday() < 5:
            continue
        weekend += 1
        if day == adjust_for_weekend(nominal, BusinessDayAdjustment.previous):
            previous += 1
        elif day == adjust_for_weekend(nominal, BusinessDayAdjustment.next):
            following += 1
    if not weekend:
        return BusinessDayAdjustment.none
    if previous and not following and previous / weekend >= 0.5:
        return BusinessDayAdjustment.previous
    if following and not previous and following / weekend >= 0.5:
        return BusinessDayAdjustment.next
    return BusinessDayAdjustment.none


def _semimonthly_days(dates: list[date]) -> tuple[int, int] | None:
    by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
    for day in dates:
        by_month[(day.year, day.month)].append(day)
    first: list[date] = []
    second: list[date] = []
    singles: list[date] = []
    for days in by_month.values():
        days.sort()
        if len(days) == 2:
            first.append(days[0])
            second.append(days[1])
        elif len(days) == 1:
            singles.append(days[0])
        else:
            return None
    if len(first) < 1:
        return None
    a = _month_day_mode(first)
    b = _month_day_mode(second)
    if a is None or b is None:
        return None
    a_day = a if a != LAST_DAY else 31
    b_day = b if b != LAST_DAY else 31
    if b_day - a_day < 10:
        return None
    for day in singles:
        if min(_day_distance(day, a), _day_distance(day, b)) > DAY_TOLERANCE:
            return None
    return a, b


# What a schedule needs besides its gaps: (weekdays, month_days, adjustment),
# or None when the dates don't have the frequency's shape.
_Shape = tuple[tuple[int, ...], tuple[int, ...], BusinessDayAdjustment]


def _weekly_shape(dates: list[date]) -> _Shape | None:
    weekday, count = Counter(d.weekday() for d in dates).most_common(1)[0]
    if count / len(dates) < MIN_FIT:
        return None
    return (weekday,), (), BusinessDayAdjustment.none


def _semimonthly_shape(dates: list[date]) -> _Shape | None:
    pair = _semimonthly_days(dates)
    if pair is None:
        return None
    return (), pair, _adjustment(dates, pair)


def _monthly_shape(dates: list[date]) -> _Shape | None:
    day = _month_day_mode(dates)
    if day is None:
        return None
    return (), (day,), _adjustment(dates, (day,))


def _yearly_shape(dates: list[date]) -> _Shape | None:
    if len({d.month for d in dates}) > 2:
        return None
    return (), (), BusinessDayAdjustment.none


_SHAPES: dict[Frequency, Callable[[list[date]], _Shape | None]] = {
    Frequency.weekly: _weekly_shape,
    Frequency.semimonthly: _semimonthly_shape,
    Frequency.monthly: _monthly_shape,
    Frequency.yearly: _yearly_shape,
}


def detect_rule(dates: list[date]) -> DetectedRule | None:
    """Find the schedule that best explains a sorted list of distinct dates."""
    gaps = _gaps(dates)
    if not gaps:
        return None
    gap_stdev = statistics.pstdev(gaps)
    found: list[DetectedRule] = []
    for spec in GAP_SPECS:
        minimum = (
            MIN_POINTS_YEARLY if spec.frequency == Frequency.yearly else MIN_POINTS
        )
        if len(dates) < minimum:
            continue
        fit = _fit(gaps, spec)
        if fit < MIN_FIT:
            continue
        shape = _SHAPES[spec.frequency](dates)
        if shape is None:
            continue
        weekdays, month_days, adjustment = shape
        found.append(
            DetectedRule(
                frequency=spec.frequency,
                interval=spec.interval,
                period_days=spec.period_days,
                fit=fit,
                gap_stdev=gap_stdev,
                weekdays=weekdays,
                month_days=month_days,
                adjustment=adjustment,
            )
        )
    if not found:
        return None
    return max(
        found, key=lambda r: (r.fit, -abs(statistics.mean(gaps) - r.period_days))
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def _in_window(points: list[TxPoint], today: date, days: int) -> list[TxPoint]:
    start = today - timedelta(days=days)
    return [p for p in points if p.date >= start]


def _detect_cluster(points: list[TxPoint], today: date) -> DetectedRule | None:
    """Try the 13-month window first, then two years for yearly series."""
    recent = _in_window(points, today, WINDOW_DAYS)
    detected = detect_rule([p.date for p in recent]) if recent else None
    if detected is not None and detected.frequency != Frequency.yearly:
        return detected
    yearly = detect_rule(
        [p.date for p in _in_window(points, today, YEARLY_WINDOW_DAYS)]
    )
    if yearly is not None and yearly.frequency == Frequency.yearly:
        return yearly
    return None


def _points_for(
    detected: DetectedRule, points: list[TxPoint], today: date
) -> list[TxPoint]:
    days = YEARLY_WINDOW_DAYS if detected.frequency == Frequency.yearly else WINDOW_DAYS
    return _in_window(points, today, days)


@dataclass(eq=False)
class _Series:
    """Clusters joined so far, the dates their price changed and their rule."""

    points: list[TxPoint]  # ascending by date
    price_changes: list[date]
    detected: DetectedRule | None


def _can_merge(a: _Series, b: _Series, today: date) -> DetectedRule | None:
    """The rule of `a` followed by `b` when both are one series, else None."""
    if not a.points[-1].date < b.points[0].date:
        return None
    detected = _detect_cluster(a.points + b.points, today)
    if detected is None:
        return None
    if any(
        own is not None and own.frequency != detected.frequency
        for own in (a.detected, b.detected)
    ):
        return None
    if not _gap_matches((b.points[0].date - a.points[-1].date).days, detected):
        return None
    return detected


def _merge_price_changes(
    clusters: list[list[TxPoint]], today: date
) -> list[tuple[list[TxPoint], list[date]]]:
    """Join the clusters of one series whose price changed.

    Clusters are ordered by start date and the earliest pair that continues
    one schedule is joined first, repeatedly. A cluster may join a later one
    that is not next to it: clusters in between that overlap it in time are
    another plan of the same merchant. Returns each series with the dates its
    price changed on.
    """
    items = [
        _Series(points, [], _detect_cluster(points, today))
        for points in sorted(
            (sorted(c, key=lambda p: p.date) for c in clusters),
            key=lambda points: points[0].date,
        )
    ]
    # Each pair is tried once: joining builds a new series, so a stale entry
    # never matches again.
    tried: dict[tuple[_Series, _Series], DetectedRule | None] = {}

    def can_merge(a: _Series, b: _Series) -> DetectedRule | None:
        if (a, b) not in tried:
            tried[(a, b)] = _can_merge(a, b, today)
        return tried[(a, b)]

    def join(i: int, j: int, detected: DetectedRule) -> None:
        a, b = items[i], items[j]
        items[i] = _Series(
            a.points + b.points,
            a.price_changes + [b.points[0].date] + b.price_changes,
            detected,
        )
        del items[j]

    def earlier_partner(i: int) -> tuple[int, DetectedRule] | None:
        """The first series before `i` that the (changed) series `i` continues."""
        for k in range(i):
            detected = can_merge(items[k], items[i])
            if detected is not None:
                return k, detected
        return None

    i = 0
    while i < len(items):
        j = i + 1
        while j < len(items):
            detected = can_merge(items[i], items[j])
            if detected is None:
                j += 1
                continue
            join(i, j, detected)
            # Series before `i` only failed against what `i` was before.
            while (partner := earlier_partner(i)) is not None:
                k, detected = partner
                join(k, i, detected)
                i, j = k, i
        i += 1
    return [(series.points, series.price_changes) for series in items]


def _gap_matches(gap: int, detected: DetectedRule) -> bool:
    for spec in GAP_SPECS:
        if spec.frequency == detected.frequency and spec.interval == detected.interval:
            return spec.low <= gap <= 2 * spec.high
    return False


def detect_series(
    points: list[TxPoint],
    merchants: dict[PointKey, Merchant],
    today: date,
) -> list[Candidate]:
    """Every recurring series in `points`, scored and fingerprinted.

    `merchants` maps `point_key(point)` to its resolved merchant; points
    without an entry have no text and are grouped by account and category.
    """
    groups: dict[tuple, list[TxPoint]] = defaultdict(list)
    for point in points:
        groups[group_key(point, merchants.get(point_key(point)))].append(point)

    candidates: list[Candidate] = []
    for key, members in groups.items():
        sample = members[0]
        merchant = merchants.get(point_key(sample))
        has_text = merchant is not None
        clusters = cluster_by_amount(dedupe_same_day(members), exact=not has_text)
        for cluster, price_changes in _merge_price_changes(clusters, today):
            detected = _detect_cluster(cluster, today)
            if detected is None:
                continue
            series = _points_for(detected, cluster, today)
            last = series[-1]
            if (today - last.date).days > detected.period_days * STALE_PERIODS:
                continue
            if merchant is not None:
                merchant_key, merchant_name = merchant.key, merchant.name
            else:
                merchant_key = "~" + ":".join(str(part) for part in key[2:])
                merchant_name = last.category_name or "Unlabeled"
            candidates.append(
                Candidate(
                    type=sample.type,
                    merchant_key=merchant_key,
                    merchant_name=merchant_name,
                    has_text=has_text,
                    points=series,
                    detected=detected,
                    confidence=base_confidence(
                        detected, [p.amount for p in series], last.date, today
                    ),
                    next_expected_date=next_expected(detected, last.date, today),
                    reasons=series_reasons(
                        detected, series, price_changes, sample.type, today
                    ),
                )
            )
    assign_fingerprints(candidates)
    return candidates
