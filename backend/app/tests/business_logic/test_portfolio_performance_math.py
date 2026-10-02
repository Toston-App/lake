from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from app.services.portfolio_performance import (
    SplitEvent,
    Valuation,
    compute_performance,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def v(day_offset, value, positions, invested="0"):
    return Valuation(
        day=(T0 + timedelta(days=day_offset)).date(),
        captured_at=T0 + timedelta(days=day_offset),
        value=Decimal(str(value)),
        invested=Decimal(str(invested)),
        positions={
            holding_id: (Decimal(str(q)), Decimal(str(val)))
            for holding_id, (q, val) in positions.items()
        },
    )


def split(hours_after_t0, ratio, holding_id=1):
    return SplitEvent(
        holding_id=holding_id,
        recorded_at=T0 + timedelta(hours=hours_after_t0),
        ratio=Decimal(str(ratio)),
    )


@pytest.mark.parametrize(
    "valuations,splits,absolute,contributions,pct",
    [
        pytest.param([v(0, 1000, {1: (10, 1000)})], [], 0, 0, 0, id="single-point"),
        pytest.param(
            [v(0, 1000, {1: (10, 1000)}), v(1, 1100, {1: (10, 1100)})],
            [],
            100,
            0,
            10,
            id="price-rise",
        ),
        pytest.param(
            [v(0, 1000, {1: (10, 1000)}), v(1, 2000, {1: (20, 2000)})],
            [],
            0,
            1000,
            0,
            id="buy-at-flat-price",
        ),
        pytest.param(
            [
                v(0, 1000, {1: (10, 1000)}),
                v(1, 1500, {1: (10, 1500)}),
                v(2, 0, {}),
            ],
            [],
            500,
            -1500,
            50,
            id="sell-winner-fully",
        ),
        pytest.param(
            [v(0, 1000, {1: (10, 1000)}), v(1, 600, {1: (5, 600)})],
            [],
            200,
            -600,
            20,
            id="partial-sell-after-rise",
        ),
        pytest.param(
            [v(0, 1000, {1: (10, 1000)}), v(1, 1000, {1: (40, 1000)})],
            [split(12, 4)],
            0,
            0,
            0,
            id="split-between",
        ),
        pytest.param(
            [v(0, 1000, {1: (10, 1000)}), v(1, 1000, {1: (40, 1000)})],
            [split(25, 4)],
            -750,
            750,
            -75,
            id="split-outside-pair-ignored",
        ),
        pytest.param(
            [
                v(0, 1000, {1: (10, 1000)}),
                v(1, 1100, {1: (10, 1100)}),
                v(2, 2200, {1: (20, 2200)}),
                v(3, 2420, {1: (20, 2420)}),
            ],
            [],
            320,
            1100,
            21,
            id="chained-twr",
        ),
        pytest.param(
            [v(0, 0, {}), v(1, 1000, {1: (10, 1000)})],
            [],
            0,
            1000,
            0,
            id="first-buy-from-empty",
        ),
        pytest.param(
            [
                v(0, 1500, {1: (10, 1000), 2: (5, 500)}),
                v(1, 500, {2: (5, 500)}),
            ],
            [],
            0,
            -1000,
            0,
            id="holding-deleted",
        ),
    ],
)
def test_compute_performance(valuations, splits, absolute, contributions, pct):
    result = compute_performance(valuations, splits)
    assert result.absolute_return == Decimal(absolute)
    assert result.net_contributions == Decimal(contributions)
    assert result.percentage_return == Decimal(pct)


def test_fx_only_move_counts_as_market_gain():
    result = compute_performance(
        [v(0, 1800, {1: (1, 1800)}), v(1, 2000, {1: (1, 2000)})], []
    )
    assert result.absolute_return == Decimal("200")
    assert result.net_contributions == Decimal("0")
    assert round(result.percentage_return, 4) == Decimal("11.1111")
