"""Joining amount clusters of one series across price changes."""

import ast
import inspect
import random
from datetime import date, timedelta

from app.core import recurrence_detection as rd
from app.core.recurrence import Frequency
from app.core.transaction_type import TransactionType

TODAY = date(2026, 11, 3)
_ids = iter(range(1, 1_000_000))


def _point(day: date, amount: float) -> rd.TxPoint:
    return rd.TxPoint(
        id=next(_ids), type=TransactionType.expense, date=day, amount=amount
    )


def _cluster(days: list[date], amount: float) -> list[rd.TxPoint]:
    return [_point(d, amount) for d in days]


def _monthly(day: int, first: tuple[int, int], count: int) -> list[date]:
    year, month = first
    out = []
    for _ in range(count):
        out.append(date(year, month, day))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _reference_merge(clusters, today):
    """The original implementation: restart from the first pair after each join."""
    items = [(sorted(c, key=lambda p: p.date), []) for c in clusters]
    merged = True
    while merged:
        merged = False
        items.sort(key=lambda item: item[0][0].date)
        for i, (a, a_reasons) in enumerate(items):
            for j in range(i + 1, len(items)):
                b, b_reasons = items[j]
                if not a[-1].date < b[0].date:
                    continue
                combined = a + b
                detected = rd._detect_cluster(combined, today)
                if detected is None:
                    continue
                spec_gap = (b[0].date - a[-1].date).days
                own = [rd._detect_cluster(a, today), rd._detect_cluster(b, today)]
                if any(
                    r is not None and r.frequency != detected.frequency for r in own
                ):
                    continue
                if not rd._gap_matches(spec_gap, detected):
                    continue
                items[i] = (combined, a_reasons + [b[0].date] + b_reasons)
                del items[j]
                merged = True
                break
            if merged:
                break
    return items


def _random_clusters(rng: random.Random) -> list[list[rd.TxPoint]]:
    """Plans of one merchant: schedules split by price changes, plus noise."""
    clusters = []
    for _ in range(rng.randint(1, 3)):
        step = rng.choice([7, 14, 15, 30, 31, 365])
        day = TODAY - timedelta(days=rng.randint(40, 700))
        days = []
        while day < TODAY or len(days) < 2:
            if rng.random() > 0.1:
                days.append(day + timedelta(days=rng.choice([0, 0, 0, -1, 1, 3])))
            day += timedelta(days=step)
        cuts = sorted(rng.sample(range(1, len(days)), min(3, len(days) - 1)))
        for start, end in zip([0, *cuts], [*cuts, len(days)], strict=False):
            if days[start:end]:
                clusters.append(_cluster(days[start:end], rng.uniform(5, 50)))
    for _ in range(rng.randint(0, 3)):
        clusters.append(
            _cluster([TODAY - timedelta(days=rng.randint(1, 500))], rng.uniform(5, 50))
        )
    rng.shuffle(clusters)
    return clusters


def test_matches_the_original_join_order_on_random_cluster_sets():
    rng = random.Random(1106)
    joined = 0
    for _ in range(400):
        clusters = _random_clusters(rng)
        expected = _reference_merge(clusters, TODAY)
        assert rd._merge_price_changes(clusters, TODAY) == expected
        joined += len(clusters) - len(expected)
    assert joined > 200  # the generated sets do exercise joins


def test_a_series_skips_an_overlapping_plan_to_join_its_new_price():
    plan = _cluster(_monthly(5, (2026, 1), 6), 9.99)
    other = _cluster(_monthly(20, (2026, 1), 10), 4.99)
    new_price = _cluster(_monthly(5, (2026, 7), 4), 12.99)
    merged = rd._merge_price_changes([other, new_price, plan], TODAY)
    assert sorted((len(points), changes) for points, changes in merged) == [
        (10, []),
        (10, [date(2026, 7, 5)]),
    ]


def test_the_earliest_cluster_joins_first_even_past_a_closer_one():
    # A consecutive fold would join Jul 5 with the Aug-Oct cluster instead.
    clusters = [
        _cluster([date(2026, 6, 5)], 225.89),
        _cluster([date(2026, 7, 5)], 282.36),
        _cluster(_monthly(5, (2026, 8), 3), 451.78),
    ]
    merged = rd._merge_price_changes(clusters, TODAY)
    assert merged == _reference_merge(clusters, TODAY)
    assert [(p[0].date, len(p), changes) for p, changes in merged] == [
        (date(2026, 6, 5), 4, [date(2026, 8, 5)]),
        (date(2026, 7, 5), 1, []),
    ]
    rule = rd._detect_cluster(merged[0][0], TODAY)
    assert rule is not None and rule.frequency == Frequency.monthly


def _count_detections(monkeypatch, merge, clusters) -> tuple[list, int]:
    calls = 0
    detect = rd._detect_cluster

    def counting(points, today):
        nonlocal calls
        calls += 1
        return detect(points, today)

    monkeypatch.setattr(rd, "_detect_cluster", counting)
    result = merge(clusters, TODAY)
    monkeypatch.setattr(rd, "_detect_cluster", detect)
    return result, calls


def test_thirty_clusters_take_a_quadratic_number_of_detections(monkeypatch):
    # 15 one-off charges that join nothing, then a weekly series whose price
    # changes every 2 weeks: each join used to rescan every earlier pair.
    start = TODAY - timedelta(days=390)
    clusters = [
        _cluster([start + timedelta(days=sum(range(k + 2)))], 100.0 + 10 * k)
        for k in range(15)
    ]
    weekly = TODAY - timedelta(weeks=30)
    clusters += [
        _cluster([weekly + timedelta(weeks=2 * k + w) for w in range(2)], 5.0 + k)
        for k in range(15)
    ]
    n = len(clusters)

    old, old_calls = _count_detections(monkeypatch, _reference_merge, clusters)
    new, new_calls = _count_detections(monkeypatch, rd._merge_price_changes, clusters)

    assert new == old
    assert len(new) == 16
    assert new_calls <= n * n
    assert new_calls * 4 < old_calls


def test_merge_does_not_restart_and_detect_rule_has_no_frequency_branches():
    merge = inspect.getsource(rd._merge_price_changes)
    assert "merged = True" not in merge
    rule = ast.parse(inspect.getsource(rd.detect_rule))
    compared = [
        node
        for node in ast.walk(rule)
        if isinstance(node, ast.If) and "Frequency.weekly" in ast.unparse(node.test)
    ]
    assert compared == []
    assert set(rd._SHAPES) == {spec.frequency for spec in rd.GAP_SPECS}
