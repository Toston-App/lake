"""Pure detection of recurring series in a user's transaction history.

The service loads transactions and resolves each description to a merchant;
this module groups them, splits each group by amount, finds a schedule that
fits the dates and scores the result. Nothing here touches the database or
the network, so every threshold can be tested with plain dates.
"""

from __future__ import annotations

import calendar
import hashlib
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from app.core.recurrence import (
    DAY_TOLERANCE,
    LAST_DAY,
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    RecurringType,
    Rule,
    add_months,
    adjust_for_weekend,
    last_day,
    next_occurrence,
    resolve_month_day,
    snap_to_nominal,
)

# Windows: yearly series need two years of history, the rest one year.
WINDOW_DAYS = 395  # ~13 months
YEARLY_WINDOW_DAYS = 760  # ~25 months

MIN_POINTS = 3
MIN_POINTS_YEARLY = 2
MIN_FIT = 0.75
AMOUNT_TOLERANCE = 0.10
# Groups without any text (no description, no place) only match exact amounts.
EXACT_AMOUNT_TOLERANCE = 0.01
# A series is considered stopped once this many periods pass without a charge.
STALE_PERIODS = 1.5
MAX_SUGGESTIONS = 50

# Payment noise removed from descriptions before comparing them (es + en).
NOISE_WORDS = frozenset(
    {
        "pago",
        "pagos",
        "payment",
        "payments",
        "pmt",
        "compra",
        "compras",
        "purchase",
        "pos",
        "card",
        "tarjeta",
        "tdc",
        "tdd",
        "debit",
        "debito",
        "credit",
        "credito",
        "cargo",
        "cargos",
        "charge",
        "transferencia",
        "transfer",
        "spei",
        "recibo",
        "ref",
        "referencia",
        "reference",
        "visa",
        "mastercard",
        "mc",
        "amex",
        "online",
        "en",
        "linea",
        "www",
        "http",
        "https",
        "com",
        "net",
        "org",
        "mx",
        "es",
        "io",
    }
)

_URL_SCHEME = re.compile(r"\b(?:https?://|www\.)")
_DOMAIN = re.compile(
    r"\b([a-z0-9-]+)\.(?:com|net|org|io|co|mx|es|tv|app)(?:\.[a-z]{2})?\b"
)
_DATE = re.compile(r"\b\d{1,4}[/.-]\d{1,2}(?:[/.-]\d{1,4})?\b")
_CARD = re.compile(r"(?:\*+|x{2,})\d{2,}")
_DIGITS = re.compile(r"\b\d{3,}\b")
_PUNCT = re.compile(r"[^a-z0-9]+")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _is_reference(token: str) -> bool:
    """Mixed letters and digits, 6+ characters: an order or reference id."""
    return (
        len(token) >= 6
        and any(ch.isdigit() for ch in token)
        and any(ch.isalpha() for ch in token)
    )


def normalize_description(text: str | None) -> str:
    """Reduce a bank description to the words that identify the counterparty."""
    if not text:
        return ""
    value = _strip_accents(text.lower())
    value = _URL_SCHEME.sub(" ", value)
    value = _DOMAIN.sub(r"\1", value)
    value = _CARD.sub(" ", value)
    value = _DATE.sub(" ", value)
    value = _DIGITS.sub(" ", value)
    value = _PUNCT.sub(" ", value)
    tokens = [
        token
        for token in value.split()
        if token not in NOISE_WORDS and not _is_reference(token) and not token.isdigit()
    ]
    return " ".join(tokens)


def slugify(text: str) -> str:
    value = _PUNCT.sub("_", _strip_accents(text.lower())).strip("_")
    return value[:64]


@dataclass(frozen=True)
class TxPoint:
    id: int
    type: RecurringType
    date: date
    amount: float
    description: str | None = None
    place_name: str | None = None
    account_id: int | None = None
    to_account_id: int | None = None  # transfers
    category_id: int | None = None
    subcategory_id: int | None = None
    place_id: int | None = None
    category_name: str | None = None


@dataclass(frozen=True)
class Merchant:
    key: str
    name: str


PointKey = tuple[RecurringType, int]


def point_key(point: TxPoint) -> PointKey:
    """Ids are per table, so an expense and an income can share one."""
    return (point.type, point.id)


def text_key(point: TxPoint) -> str:
    """Normalized text used to resolve the merchant; empty if there is none."""
    return normalize_description(point.description) or normalize_description(
        point.place_name
    )


@dataclass(frozen=True)
class DetectedRule:
    frequency: Frequency
    interval: int
    period_days: float
    fit: float
    gap_stdev: float
    weekdays: tuple[int, ...] = ()
    month_days: tuple[int, ...] = ()
    adjustment: BusinessDayAdjustment = BusinessDayAdjustment.none

    def rule(self, start: date) -> Rule:
        return Rule(
            frequency=self.frequency,
            start_date=start,
            interval=self.interval,
            weekdays=self.weekdays,
            monthly_mode=MonthlyMode.day_of_month,
            month_days=self.month_days,
            adjustment=self.adjustment,
        )


@dataclass
class Candidate:
    type: RecurringType
    merchant_key: str
    merchant_name: str
    has_text: bool
    points: list[TxPoint]  # ascending by date
    detected: DetectedRule
    confidence: float
    next_expected_date: date | None
    reasons: list[str] = field(default_factory=list)
    fingerprint: str = ""
    kind: str | None = None

    @property
    def latest(self) -> TxPoint:
        return self.points[-1]

    @property
    def amounts(self) -> list[float]:
        return [p.amount for p in self.points]

    @property
    def median_amount(self) -> float:
        return statistics.median(self.amounts)


# ---------------------------------------------------------------------------
# Grouping and amount clustering
# ---------------------------------------------------------------------------
def group_key(point: TxPoint, merchant: Merchant | None) -> tuple:
    if point.type == RecurringType.transfer:
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


def missed_dates(detected: DetectedRule, last_date: date, today: date) -> list[date]:
    """Dates the series was due on after its last charge and before today."""
    rule = detected.rule(last_date)
    nominal = snap_to_nominal(rule, last_date) or last_date
    out = []
    occurrence = next_occurrence(rule, after_nominal=nominal, last_actual=last_date)
    while occurrence is not None and occurrence.actual < today:
        out.append(occurrence.actual)
        occurrence = next_occurrence(
            rule, after_nominal=occurrence.nominal, last_actual=occurrence.actual
        )
    return out


def next_expected(detected: DetectedRule, last_date: date, today: date) -> date | None:
    """First date the template will create: missed dates before today are skipped."""
    rule = detected.rule(last_date)
    nominal = snap_to_nominal(rule, last_date) or last_date
    occurrence = next_occurrence(
        rule,
        after_nominal=nominal,
        last_actual=max(last_date, today - timedelta(days=1)),
    )
    return occurrence.actual if occurrence else None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def recency_score(last_date: date, period_days: float, today: date) -> float:
    elapsed = (today - last_date).days
    if elapsed <= period_days:
        return 1.0
    stale = period_days * STALE_PERIODS
    if elapsed >= stale:
        return 0.0
    return (stale - elapsed) / (stale - period_days)


def amount_stability(amounts: list[float]) -> float:
    median = statistics.median(amounts)
    if median <= 0 or len(amounts) < 2:
        return 1.0
    return 1 - min(statistics.pstdev(amounts) / median, 1.0)


def base_confidence(
    detected: DetectedRule, amounts: list[float], last_date: date, today: date
) -> float:
    return round(
        0.45 * detected.fit
        + 0.25 * amount_stability(amounts)
        + 0.20 * min(len(amounts) / 6, 1.0)
        + 0.10 * recency_score(last_date, detected.period_days, today),
        4,
    )


def fingerprint(
    type_: RecurringType,
    merchant_key: str,
    account_id: int | None,
    to_account_id: int | None,
    frequency: Frequency,
    interval: int,
    extra: str = "",
) -> str:
    parts = [
        type_.value,
        merchant_key,
        str(account_id or ""),
        str(to_account_id or ""),
        frequency.value,
        str(interval),
    ]
    if extra:
        parts.append(extra)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


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


def _month_label(day: date) -> str:
    return f"{calendar.month_abbr[day.month]} {day.year}"


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


_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _ordinal(day: int) -> str:
    if day == LAST_DAY:
        return "the last day of the month"
    suffix = (
        "th"
        if 11 <= day % 100 <= 13
        else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )
    return f"the {day}{suffix}"


def _schedule_reason(detected: DetectedRule, count: int, type_: RecurringType) -> str:
    noun = {
        RecurringType.expense: "charges",
        RecurringType.income: "payments",
        RecurringType.transfer: "transfers",
    }[type_]
    if detected.frequency == Frequency.weekly:
        every = "every week" if detected.interval == 1 else "every 2 weeks"
        return f"{count} {noun}, {every} on {_WEEKDAYS[detected.weekdays[0]]}"
    if detected.frequency in (Frequency.monthly, Frequency.semimonthly):
        days = " and ".join(_ordinal(d) for d in detected.month_days)
        return f"{count} {noun} on {days}"
    return f"{count} {noun}, once a year"


def _reasons(
    detected: DetectedRule,
    series: list[TxPoint],
    price_changes: list[date],
    type_: RecurringType,
    today: date,
) -> list[str]:
    """Why a series is suggested: its schedule, price changes and missed dates."""
    return (
        [_schedule_reason(detected, len(series), type_)]
        + [
            f"price changed in {_month_label(changed)}"
            for changed in price_changes
            if changed > series[0].date
        ]
        + [
            f"nothing recorded on {missed.isoformat()}"
            for missed in missed_dates(detected, series[-1].date, today)
        ]
    )


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
                    reasons=_reasons(
                        detected, series, price_changes, sample.type, today
                    ),
                )
            )
    _assign_fingerprints(candidates)
    return candidates


def _assign_fingerprints(candidates: list[Candidate]) -> None:
    """Fingerprint each candidate; series sharing one get their amount added."""
    for candidate in candidates:
        candidate.fingerprint = _fingerprint_for(candidate)
    counts = Counter(c.fingerprint for c in candidates)
    for candidate in candidates:
        if counts[candidate.fingerprint] > 1:
            candidate.fingerprint = _fingerprint_for(
                candidate, extra=str(round(candidate.median_amount))
            )


def _fingerprint_for(candidate: Candidate, extra: str = "") -> str:
    latest = candidate.latest
    is_transfer = candidate.type == RecurringType.transfer
    return fingerprint(
        candidate.type,
        candidate.merchant_key,
        latest.account_id if is_transfer else None,
        latest.to_account_id if is_transfer else None,
        candidate.detected.frequency,
        candidate.detected.interval,
        extra,
    )


def apply_classification(
    candidate: Candidate, *, is_commitment: bool, confidence: float, kind: str
) -> Candidate:
    """Combine the deterministic score with the classifier's verdict."""
    if is_commitment:
        final = candidate.confidence * (0.6 + 0.4 * confidence)
    else:
        final = candidate.confidence * (1 - 0.7 * confidence)
    return replace(candidate, confidence=round(final, 4), kind=kind)
