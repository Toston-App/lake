"""The data recurring-series detection works on: points, merchants, rules, candidates.

Shared by `app.core.recurrence_scoring` and `app.core.recurrence_detection`;
imports neither, so both can depend on it without a cycle.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from app.core.recurrence import BusinessDayAdjustment, Frequency, MonthlyMode, Rule
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
