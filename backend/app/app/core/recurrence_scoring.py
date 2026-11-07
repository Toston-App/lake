"""The detected-series model and how a series is scored and explained.

Detection (`app.core.recurrence_detection`) builds `Candidate`s out of
`TxPoint`s; this module holds those types, the confidence score, the reasons
shown to the user and the fingerprint that identifies a suggestion.
"""

from __future__ import annotations

import calendar
import hashlib
import statistics
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from app.core.recurrence import (
    LAST_DAY,
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    Rule,
    next_occurrence,
    snap_to_nominal,
)
from app.core.transaction_type import TransactionType

# A series is considered stopped once this many periods pass without a charge.
STALE_PERIODS = 1.5


@dataclass(frozen=True)
class TxPoint:
    id: int
    type: TransactionType
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


PointKey = tuple[TransactionType, int]


def point_key(point: TxPoint) -> PointKey:
    """Ids are per table, so an expense and an income can share one."""
    return (point.type, point.id)


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
    type: TransactionType
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
    type_: TransactionType,
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


def _month_label(day: date) -> str:
    return f"{calendar.month_abbr[day.month]} {day.year}"


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


def _schedule_reason(detected: DetectedRule, count: int, type_: TransactionType) -> str:
    noun = {
        TransactionType.expense: "charges",
        TransactionType.income: "payments",
        TransactionType.transfer: "transfers",
    }[type_]
    if detected.frequency == Frequency.weekly:
        every = "every week" if detected.interval == 1 else "every 2 weeks"
        return f"{count} {noun}, {every} on {_WEEKDAYS[detected.weekdays[0]]}"
    if detected.frequency in (Frequency.monthly, Frequency.semimonthly):
        days = " and ".join(_ordinal(d) for d in detected.month_days)
        return f"{count} {noun} on {days}"
    return f"{count} {noun}, once a year"


def series_reasons(
    detected: DetectedRule,
    series: list[TxPoint],
    price_changes: list[date],
    type_: TransactionType,
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


def assign_fingerprints(candidates: list[Candidate]) -> None:
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
    is_transfer = candidate.type == TransactionType.transfer
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
