"""
Contribution-neutral, holdings-based time-weighted return (TWR).

For each pair of consecutive valuations, the market gain is what the units held
at the earlier valuation earned by the later one, priced at the later unit
value. That covers both price and currency moves. Everything else in the value
change is money moved in or out (buys, sells, deletes, edits, transfers). Daily
returns are chained into the period's percentage return.

Known approximations:
- Units bought during a day earn no gain until the next snapshot.
- A position sold in full loses the price move between its last snapshot and
  the sale.
- The sale price of a partial sell is assumed to equal the next snapshot's unit
  value.
- Dividends are not counted as return.
- Splits are detected only via SPLIT transactions. A split entered by editing
  quantity with PUT /holdings shows as one day with a large loss.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class Valuation:
    """Portfolio state at one moment, already expressed in the response currency."""

    day: date
    captured_at: datetime  # tz-aware
    value: Decimal
    invested: Decimal  # combined cost basis
    positions: dict[int, tuple[Decimal, Decimal]]  # holding_id -> (quantity, value)


@dataclass(frozen=True)
class SplitEvent:
    holding_id: int
    recorded_at: datetime  # tz-aware
    ratio: Decimal  # 4 for a 4:1 split


@dataclass(frozen=True)
class PerformanceResult:
    absolute_return: Decimal
    net_contributions: Decimal
    percentage_return: Decimal  # percent, e.g. 12.5


def _split_factor(
    holding_id: int, prev: Valuation, cur: Valuation, splits: Sequence[SplitEvent]
) -> Decimal:
    factor = ONE
    for split in splits:
        if (
            split.holding_id == holding_id
            and prev.captured_at < split.recorded_at <= cur.captured_at
        ):
            factor *= split.ratio
    return factor


def market_gain(
    prev: Valuation, cur: Valuation, splits: Sequence[SplitEvent]
) -> Decimal:
    """Gain earned between `prev` and `cur` by the units held at `prev`."""
    gain = ZERO
    for holding_id, (q0, v0) in prev.positions.items():
        if holding_id not in cur.positions:
            continue
        q1, v1 = cur.positions[holding_id]
        if q0 <= 0 or q1 <= 0:
            continue
        # Pre-split units expressed in post-split units.
        q0_adj = q0 * _split_factor(holding_id, prev, cur, splits)
        gain += q0_adj * (v1 / q1) - v0
    return gain


def compute_performance(
    valuations: Sequence[Valuation], splits: Sequence[SplitEvent]
) -> PerformanceResult:
    """Chain per-pair market gains; `valuations` must be sorted by captured_at."""
    if len(valuations) < 2:
        return PerformanceResult(ZERO, ZERO, ZERO)

    growth = ONE
    total_gain = ZERO
    for prev, cur in pairwise(valuations):
        gain = market_gain(prev, cur, splits)
        total_gain += gain
        period_return = gain / prev.value if prev.value > 0 else ZERO
        growth *= ONE + period_return

    start, end = valuations[0], valuations[-1]
    return PerformanceResult(
        absolute_return=total_gain,
        net_contributions=(end.value - start.value) - total_gain,
        percentage_return=(growth - ONE) * 100,
    )
