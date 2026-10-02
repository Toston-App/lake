# Plan 002: Add `GET /investments/portfolio/performance` with a contribution-neutral, time-weighted return

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**, from the repo root:
> `git diff --stat d0e79be..HEAD -- backend/app/app/api/api_v1/endpoints/portfolio.py backend/app/app/schemas/portfolio.py backend/app/app/schemas/__init__.py backend/app/app/utilities/investment_telemetry.py backend/app/tests/api/test_investment_telemetry.py backend/app/tests/api/test_investment_portfolio.py INVESTMENTS_API.md`
> Plan 001 legitimately changes `INVESTMENTS_STRUCTURE.md` and adds new files;
> any change to the files listed above means you must compare the "Current
> state" excerpts against the live code first; on a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW–MED (read-only endpoint; the return math is the risky part and is fully unit-tested)
- **Depends on**: `plans/001-daily-portfolio-snapshots.md` (must be DONE)
- **Category**: direction
- **Planned at**: commit `d0e79be`, 2026-10-01 (revised same day after a cold review)

## Why this matters

Users see what their portfolio is worth now (`/portfolio/summary`) but not how
it got there or how well it performed. Plan 001 stores one snapshot per user per
day, with per-holding positions. This plan exposes a chart-ready series plus a
headline return for a period (1W … ALL). Today's point is computed live, so the
chart ends at the same number as the summary card.

**The return must reflect market performance only.** Money moving in or out is
not performance, whether by buying, selling, deleting a holding, editing it, or
transferring. A naive "change in value minus change in cost basis" gets this
wrong in two ways:

- It reports a sale of a winning position as a loss.
- It hides gains that come from USD/MXN moves.

This plan uses a **holdings-based time-weighted return (TWR)**:

- For each pair of consecutive days, the market gain is what the units held at
  the start of the day earned. That covers both price and currency moves.
- Everything else in the value change is money in or out.
- Daily returns are chained.

This is the same kind of figure brokers show.

## Current state

Paths are relative to the repo root; Python package root is `backend/app/app/`
(imported as `app.*`), tests in `backend/app/tests/` (imported as `tests.*`).
Pydantic is **2.13**, SQLAlchemy **1.4.54**.

### From plan 001 (confirm these exist before starting; STOP if not)

- `app/models/portfolio_snapshot.py`:
  - `PortfolioSnapshot`: `owner_id`, `snapshot_date: date`, `captured_at: datetime`, `total_value_usd`, `total_value_mxn`, `total_invested_usd`, `total_invested_mxn` (**raw** cost basis of USD-cost / MXN-cost holdings, unconverted), `usd_mxn_rate`, `holdings_count`, and relationship `positions`.
  - `PortfolioSnapshotHolding`: `holding_id`, `asset_id`, `quantity`, `value_usd`, `value_mxn`.
- `crud.portfolio_snapshot`:
  - `get_range(db, *, owner_id, start_date: date | None)` returns rows in ascending date order, with `positions` loaded.
  - `get_latest_on_or_before(db, *, owner_id, day: date)`.
- `app/services/portfolio_snapshot.py`:
  - `PortfolioTotals`, with `total_value_*`, `total_invested_*` (raw), `total_invested_combined_*`, `holdings_count`, and `positions: tuple[PositionValue, ...]`.
  - `PositionValue`, with `holding_id`, `asset_id`, `quantity`, `value_usd`, `value_mxn`.
  - `compute_portfolio_totals(holdings, usd_mxn_rate)`.
  - `combined_invested(total_invested_usd, total_invested_mxn, usd_mxn_rate, currency)`.
  - `portfolio_today()`.

### Existing code

- `backend/app/app/schemas/portfolio.py:116-135` — **unused** placeholder schemas
  (only re-exported at `schemas/__init__.py:102-103`). Confirm with
  `grep -rn "PortfolioPerformance\|PerformanceDataPoint" app --include='*.py'` (from `backend/app/`).
  You will replace:

  ```python
  # Performance over time
  class PerformanceDataPoint(BaseModel):
      """Single data point in performance history."""

      date: datetime
      value_usd: float
      value_mxn: float
      gain_loss: float
      gain_loss_pct: float


  class PortfolioPerformance(BaseModel):
      """Portfolio performance over time."""

      period: str  # "1D", "1W", "1M", "3M", "1Y", "ALL"
      start_value: float
      end_value: float
      absolute_return: float
      percentage_return: float
      data_points: list[PerformanceDataPoint]
  ```

- `backend/app/app/api/api_v1/endpoints/portfolio.py` — all portfolio routes (file
  ends at line 600). **Exemplars**: `get_portfolio_summary` (lines 49-121) and
  `get_top_holdings` (lines 543-600). Conventions:
  - signature `request: Request, db = Depends(deps.async_get_db), current_user = Depends(deps.get_current_active_user)`; query params via `Query(...)`;
  - FX via `_trusted_usd_mxn_rate()` (lines 40-46) → `HTTPException(503)` if unavailable;
  - telemetry: `with investment_stage(request, "database_query"):`, `with investment_stage(request, "fx_lookup"):`, `begin_investment_stage(request, "aggregation")` / `complete_investment_stage(request, "aggregation")`, then `complete_investment_event(request, result_count=..., holdings_count=...)`;
  - `Decimal` internally, `round(x, 2)` only when building the response; response models use `float`;
  - `Currency` is already imported (line 15), `ZERO = Decimal("0")` exists (line 37);
  - auth and the feature gate come from the parent router (`endpoints/investments.py`); add nothing.

- Splits: `InvestmentTransaction` (`app/models/investment_transaction.py`) with
  `transaction_type == TransactionType.SPLIT` stores the split **ratio** in
  `quantity` (e.g. `4` for 4:1) and multiplies the holding's quantity
  (`investment_transactions.py:798-810`). `created_at` (server default `now()`)
  is when it was recorded, which is when the holding's quantity actually changed.
  Import with `from app.models.investment_transaction import InvestmentTransaction, TransactionType`.

- `backend/app/app/utilities/investment_telemetry.py:22-51` — `_RESOURCE_BY_OPERATION`
  maps every investments route function name to a resource; it ends with
  `"get_top_holdings": "portfolio",`.
- `backend/app/tests/api/test_investment_telemetry.py:17-89` — `EXPECTED_OPERATIONS`
  and `assert len(_RESOURCE_BY_OPERATION) == 28` (line 89). Adding a route → 29.

- Test helper `create_test_investment_transaction(db, *, owner_id, account_id, holding_id, transaction_type=..., quantity=..., price_per_unit=...)`
  exists in `backend/app/tests/utils.py:357`.

### Conventions

- Conventional commits (`feat: ...`, `tests: ...`).
- Ruff: ~349 pre-existing errors repo-wide; lint only files you touch (all
  existing files this plan modifies currently pass `ruff check` and `ruff format --check`).

## Commands you will need

Run from `backend/` unless noted.

| Purpose | Command | Expected on success |
|---|---|---|
| Start test DB (repo root) | `docker compose -f docker-compose.test.yml up -d` | `db-test` healthy on 5433 |
| Math unit tests | `uv run pytest app/tests/business_logic/test_portfolio_performance_math.py -v` | all pass |
| API tests | `uv run pytest app/tests/api/test_investment_performance.py -v` | all pass |
| Telemetry inventory | `uv run pytest app/tests/api/test_investment_telemetry.py -q` | all pass |
| Investments regression | `uv run pytest app/tests -k "investment or portfolio" -q` | 0 failed |
| Lint / format touched files | `uv run ruff check <files>` / `uv run ruff format --check <files>` | pass |

## Scope

**In scope**:
- `backend/app/app/services/portfolio_performance.py` (create — pure math)
- `backend/app/app/schemas/portfolio.py`, `backend/app/app/schemas/__init__.py`
- `backend/app/app/api/api_v1/endpoints/portfolio.py` (one route + private helpers at end of file)
- `backend/app/app/utilities/investment_telemetry.py` (one entry)
- `backend/app/tests/api/test_investment_telemetry.py` (set + count)
- `backend/app/tests/business_logic/test_portfolio_performance_math.py` (create)
- `backend/app/tests/api/test_investment_performance.py` (create)
- `INVESTMENTS_STRUCTURE.md`, `INVESTMENTS_API.md`
- `plans/README.md` (status row only)

**Out of scope**:
- Existing portfolio routes: no behavior changes or refactors.
- Plan 001's files (models, crud, snapshot service, worker). If they need changes, STOP.
- Frontend (`frontend/` is throwaway prototypes; the real dashboard is another repo).
- Dividends as return, backfill, per-account / per-holding performance, benchmarks: deferred.

## Git workflow

- Branch: `advisor/002-portfolio-performance` off the branch containing plan 001.
- e.g. `feat: add portfolio performance endpoint`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Write the pure return math

Create `backend/app/app/services/portfolio_performance.py`. No DB, no I/O, all `Decimal`:

```python
@dataclass(frozen=True)
class Valuation:
    """Portfolio state at one moment, already expressed in the response currency."""

    day: date
    captured_at: datetime                      # tz-aware
    value: Decimal
    invested: Decimal                          # combined cost basis
    positions: dict[int, tuple[Decimal, Decimal]]  # holding_id -> (quantity, value)


@dataclass(frozen=True)
class SplitEvent:
    holding_id: int
    recorded_at: datetime                      # tz-aware
    ratio: Decimal                             # 4 for a 4:1 split


@dataclass(frozen=True)
class PerformanceResult:
    absolute_return: Decimal
    net_contributions: Decimal
    percentage_return: Decimal                 # percent, e.g. 12.5


def market_gain(prev: Valuation, cur: Valuation, splits: Sequence[SplitEvent]) -> Decimal: ...


def compute_performance(
    valuations: Sequence[Valuation], splits: Sequence[SplitEvent]
) -> PerformanceResult: ...
```

`market_gain(prev, cur, splits)` is the gain earned by the units held at `prev`:

```
gain = 0
for holding_id in prev.positions keys that are also in cur.positions:
    q0, v0 = prev.positions[holding_id]
    q1, v1 = cur.positions[holding_id]
    if q0 <= 0 or q1 <= 0: continue
    factor = product of s.ratio for s in splits
             where s.holding_id == holding_id
             and prev.captured_at < s.recorded_at <= cur.captured_at
             (1 if none)
    q0_adj = q0 * factor            # pre-split units expressed in post-split units
    unit_value_now = v1 / q1
    gain += q0_adj * unit_value_now - v0
return gain
```

`compute_performance(valuations, splits)` (valuations sorted by `captured_at`):

```
if len(valuations) < 2: return PerformanceResult(0, 0, 0)
growth = 1; total_gain = 0
for prev, cur in consecutive pairs:
    g = market_gain(prev, cur, splits)
    total_gain += g
    r = g / prev.value if prev.value > 0 else 0
    growth *= (1 + r)
start, end = valuations[0], valuations[-1]
return PerformanceResult(
    absolute_return=total_gain,
    net_contributions=(end.value - start.value) - total_gain,
    percentage_return=(growth - 1) * 100,
)
```

Put a module docstring stating the method and its known approximations (copy
the "Known approximations" bullets from Maintenance notes).

**Verify**: `uv run ruff check app/app/services/portfolio_performance.py` → passes.

### Step 2: Unit-test the math (before wiring the route)

Create `backend/app/tests/business_logic/test_portfolio_performance_math.py`
(pure tests, no fixtures; follow the style of
`tests/business_logic/test_investment_position_math.py`). Build `Valuation`s with
a helper `v(day_offset, value, positions, invested=...)` using
`captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_offset)`.
All holding ids below are `1` unless stated. Expected values are exact.

| # | Scenario | Valuations (q, value) | Splits | absolute | contributions | pct |
|---|---|---|---|---|---|---|
| 1 | single point | d0 (10, 1000) | — | 0 | 0 | 0 |
| 2 | price rise | d0 (10, 1000) → d1 (10, 1100) | — | 100 | 0 | 10 |
| 3 | buy at flat price | d0 (10, 1000) → d1 (20, 2000) | — | 0 | 1000 | 0 |
| 4 | sell a winner fully | d0 (10, 1000) → d1 (10, 1500) → d2 *(holding absent, value 0)* | — | 500 | −1500 | 50 |
| 5 | partial sell after rise | d0 (10, 1000) → d1 (5, 600) | — | 200 | −600 | 20 |
| 6 | FX only (values in MXN) | d0 (1, 1800) → d1 (1, 2000) | — | 200 | 0 | 11.111… (assert `round(pct, 4) == Decimal("11.1111")`) |
| 7 | split recorded between | d0 (10, 1000) → d1 (40, 1000) | ratio 4 at d0 + 12h | 0 | 0 | 0 |
| 8 | split outside the pair is ignored | same as 7 | ratio 4 at d1 + 1h | −750 | 750 | −75 |
| 9 | chained TWR | d0 (10, 1000) → d1 (10, 1100) → d2 (20, 2200) → d3 (20, 2420) | — | 320 | 1100 | 21 |
| 10 | first buy from empty | d0 *(no positions, value 0)* → d1 (10, 1000) | — | 0 | 1000 | 0 |
| 11 | holding deleted (correction) | d0 (10, 1000) + holding 2 (5, 500), total 1500 → d1 only holding 2 (5, 500), total 500 | — | 0 | −1000 | 0 |

Hand-check of #9: d1 gain 100 (r=10%); d2 gain 10×110−1100=0; d3 gain
20×121−2200=220 (r=10%); TWR=1.1×1.0×1.1−1=21%; contributions=(2420−1000)−320=1100.

**Verify**: `uv run pytest app/tests/business_logic/test_portfolio_performance_math.py -v` → 11 passed.
If any row fails, fix the implementation, not the table. If you believe a row's
expected value is wrong, STOP and report.

### Step 3: Replace the placeholder schemas

In `backend/app/app/schemas/portfolio.py`, replace the block shown in "Current
state" with:

```python
# Performance over time
class PerformancePeriod(str, Enum):
    ONE_WEEK = "1W"
    ONE_MONTH = "1M"
    THREE_MONTHS = "3M"
    SIX_MONTHS = "6M"
    YEAR_TO_DATE = "YTD"
    ONE_YEAR = "1Y"
    ALL = "ALL"


class PerformanceDataPoint(BaseModel):
    """Portfolio valuation on one day, in the response currency."""

    date: dt.date
    value: float
    invested: float
    gain_loss: float
    gain_loss_pct: float


class PortfolioPerformance(BaseModel):
    """Value series and time-weighted, contribution-neutral return for a period."""

    period: PerformancePeriod
    currency: Currency
    start_date: dt.date
    end_date: dt.date
    start_value: float
    end_value: float
    net_contributions: float
    absolute_return: float
    percentage_return: float
    data_points: list[PerformanceDataPoint]
```

Add `import datetime as dt` and `from enum import Enum`, and add `Currency` to the
existing `from app.models.asset import ...` line. Use `dt.date` (a field named
`date` with a bare `date` annotation breaks Pydantic). Keep the existing
`from datetime import datetime` import. Add `PerformancePeriod` to the
`from .portfolio import (...)` list in `schemas/__init__.py` (alphabetical).

**Verify**: `uv run ruff check app/app/schemas/portfolio.py app/app/schemas/__init__.py` → passes.

### Step 4: Add the route

Append to `portfolio.py` after `get_top_holdings`. Private helpers first:

```python
_PERIOD_DAYS = {
    PerformancePeriod.ONE_WEEK: 7,
    PerformancePeriod.ONE_MONTH: 30,
    PerformancePeriod.THREE_MONTHS: 90,
    PerformancePeriod.SIX_MONTHS: 182,
    PerformancePeriod.ONE_YEAR: 365,
}


def _period_start(period: PerformancePeriod, today: date) -> date | None:
    """First day of the window; None means no lower bound (ALL)."""
    if period == PerformancePeriod.ALL:
        return None
    if period == PerformancePeriod.YEAR_TO_DATE:
        return date(today.year, 1, 1)
    return today - timedelta(days=_PERIOD_DAYS[period])


def _snapshot_valuation(snapshot: PortfolioSnapshot, currency: Currency) -> Valuation: ...
def _live_valuation(totals: PortfolioTotals, rate: Decimal, currency: Currency, today: date) -> Valuation: ...
```

- `_snapshot_valuation`: `value = total_value_usd|mxn`;
  `invested = combined_invested(s.total_invested_usd, s.total_invested_mxn, s.usd_mxn_rate, currency)`;
  `positions = {p.holding_id: (p.quantity, p.value_usd|value_mxn) for p in s.positions}`;
  `day = s.snapshot_date`, `captured_at = s.captured_at`.
- `_live_valuation`: same from `PortfolioTotals` using the live rate;
  `captured_at = datetime.now(timezone.utc)`, `day = today`.

Route:

```python
@router.get("/performance", response_model=PortfolioPerformance)
async def get_portfolio_performance(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    period: PerformancePeriod = Query(PerformancePeriod.ONE_MONTH),
    currency: Currency = Query(Currency.USD),
) -> Any:
```

Behavior, in order:
1. `today = portfolio_today()`; `start = _period_start(period, today)`.
2. `with investment_stage(request, "database_query"):`
   - `snapshots = await crud.portfolio_snapshot.get_range(db, owner_id=current_user.id, start_date=start)`;
   - if `start` is not None: `baseline = await crud.portfolio_snapshot.get_latest_on_or_before(db, owner_id=current_user.id, day=start)`; if it exists and its `id` is not already in `snapshots`, prepend it. (This makes "1M" measure from the last known value at or before the window start, instead of silently starting later.)
   - drop any snapshot whose `snapshot_date == today` (the live point replaces it);
   - `holdings = await crud.holding.get_by_owner(db, owner_id=current_user.id, limit=None)`;
   - splits: `select(InvestmentTransaction).where(InvestmentTransaction.owner_id == current_user.id, InvestmentTransaction.transaction_type == TransactionType.SPLIT)`, plus `InvestmentTransaction.created_at > snapshots[0].captured_at` when `snapshots` is non-empty → map to `SplitEvent(holding_id, created_at, quantity)`.
3. `with investment_stage(request, "fx_lookup"):` `rate = await _trusted_usd_mxn_rate()`.
4. `begin_investment_stage(request, "aggregation")`.
5. `valuations = [_snapshot_valuation(s, currency) for s in snapshots] + [_live_valuation(compute_portfolio_totals(holdings, rate), rate, currency, today)]`.
6. `result = compute_performance(valuations, splits)`.
7. Data points: one per valuation:
   - `date=v.day`, `value=v.value`, `invested=v.invested`;
   - `gain_loss = v.value - v.invested`;
   - `gain_loss_pct = gain_loss / v.invested * 100`, or `0` when `invested <= 0`.
8. `complete_investment_stage(request, "aggregation")`;
   `complete_investment_event(request, result_count=len(valuations), holdings_count=len(holdings))`.
9. Return `PortfolioPerformance`:
   - `start_date` and `start_value` come from `valuations[0]`; `end_date` and `end_value` from `valuations[-1]`;
   - `net_contributions`, `absolute_return` and `percentage_return` come from `result`;
   - pass every money and percent field through `round(x, 2)`.

Imports to add:
- `from datetime import date, datetime, timedelta, timezone`, replacing the current datetime import.
- `from sqlalchemy import select`.
- `PortfolioSnapshot`.
- `InvestmentTransaction`, `TransactionType`.
- `PerformanceDataPoint`, `PerformancePeriod`, `PortfolioPerformance` in the existing schema import block.
- `compute_portfolio_totals`, `combined_invested`, `portfolio_today`, `PortfolioTotals` from `app.services.portfolio_snapshot`.
- `Valuation`, `SplitEvent`, `compute_performance` from `app.services.portfolio_performance`.

**Verify**: `uv run ruff check app/app/api/api_v1/endpoints/portfolio.py && uv run ruff format --check app/app/api/api_v1/endpoints/portfolio.py app/app/schemas/portfolio.py app/app/services/portfolio_performance.py` → passes.

### Step 5: Register the operation for telemetry

- `investment_telemetry.py`: add `"get_portfolio_performance": "portfolio",` after `"get_top_holdings": "portfolio",`.
- `test_investment_telemetry.py`: add `"get_portfolio_performance"` to `EXPECTED_OPERATIONS`; change `== 28` to `== 29`.

**Verify**: `uv run pytest app/tests/api/test_investment_telemetry.py -q` → all pass.

### Step 6: Document the endpoint

- `INVESTMENTS_STRUCTURE.md`:
  - **"Portfolio analytics" table:** add `| GET | /portfolio/performance | Value series and time-weighted return for 1W/1M/3M/6M/YTD/1Y/ALL |`.
  - **"Current Limitations" item 6:** replace it with these points:
    - History starts on the snapshot worker's deploy date, with no backfill.
    - The return is a holdings-based TWR.
    - Dividends are not counted.
    - Quantity edits made via `PUT /holdings` that actually represent a split will distort that day.
  - **"Testing Structure":** change "28-route" to "29-route".
- `INVESTMENTS_API.md`:
  - **Under "### Portfolio Analytics Endpoints":** add a numbered subsection at the end, in the same format as "#### 1. Portfolio Summary". Include:
    - the endpoint;
    - the `period` (default `1M`) and `currency` (default `USD`) query params;
    - the TypeScript response schema;
    - a short JSON example;
    - two sentences explaining that `percentage_return` is time-weighted (excludes deposits and withdrawals) and that `net_contributions` is money added minus money withdrawn, at market value.
  - **"Quick Reference Table":** add `| GET | /portfolio/performance | Portfolio performance over time |`.

**Verify**: `grep -c "portfolio/performance" ../INVESTMENTS_STRUCTURE.md ../INVESTMENTS_API.md` → each ≥ 1.

## Test plan (API)

Create `backend/app/tests/api/test_investment_performance.py`, modeled on
`tests/api/test_investment_portfolio.py`. Setup:
- Use the `# ruff: noqa: ARG001` header and `PREFIX = "/api/v1/investments/portfolio"`.
- Use the `client`, `db_session`, `test_user` and `enable_investments` fixtures. The last one mocks USD/MXN to `Decimal("18")`.
- Build holdings via `create_test_account`, `create_test_asset` and `create_test_holding`, then set `current_value*` directly and commit.
- Insert past snapshots by adding `PortfolioSnapshot(...)` rows (with `PortfolioSnapshotHolding` children where the case needs positions) to `db_session` and committing.
- **Fill every NOT NULL column:** `captured_at`, `total_value_usd`, `total_value_mxn`, `total_invested_usd`, `total_invested_mxn`, `usd_mxn_rate=Decimal("18")` and `holdings_count`.
- Compute dates relative to `portfolio_today()` and set `captured_at` to `datetime.now(timezone.utc) - timedelta(days=N)` for a snapshot N days ago. Never hard-code calendar dates.

Cases:
1. **Empty**: no holdings, no snapshots → 200, one data point, all returns 0.
2. **Live point = summary**: two-holding portfolio, no snapshots → `end_value`
   equals `/portfolio/summary` `total_value_usd`, and with `currency=MXN` equals
   `total_value_mxn`.
3. **Window and baseline**: snapshots at today−40, −20, −5 →
   `period=1M`: 4 points, `start_date == today−40` (baseline);
   `period=1W`: 3 points, `start_date == today−20`;
   `period=ALL`: 4 points.
4. **Today's stored snapshot replaced**: a snapshot dated today with a different
   value → exactly one point dated today, equal to the live value.
5. **Market gain end-to-end**: one USD holding, snapshot yesterday with position
   `(holding_id=h.id, quantity=10, value_usd=1000, value_mxn=18000)`; live holding
   `quantity=10, current_value_usd=1100, current_value_mxn=19800` →
   `absolute_return 100`, `percentage_return 10`, `net_contributions 0`.
6. **Split end-to-end**: snapshot yesterday with position `(q=10, value_usd=1000)`;
   add a SPLIT transaction with `create_test_investment_transaction(..., transaction_type=TransactionType.SPLIT, quantity=Decimal("4"))`;
   live holding `quantity=40, current_value_usd=1000` → `absolute_return 0`.
   (Inside the test transaction `created_at` is the transaction start time, which
   is after yesterday's `captured_at` and before the live point — that is why
   this works.)
7. **Isolation**: snapshots for a second user (`create_test_user`) never appear.
8. **Invalid period**: `period=2D` → 422.
9. **FX unavailable**: patch `CurrencyConverter.get_usd_to_mxn_rate` with
   `AsyncMock(side_effect=CurrencyRateUnavailable("x"))` after `enable_investments` → 503.

**Verify**: `uv run pytest app/tests/api/test_investment_performance.py -v` → 9 passed.

## Done criteria

- [ ] `uv run pytest app/tests/business_logic/test_portfolio_performance_math.py -q` → 11 passed
- [ ] `uv run pytest app/tests/api/test_investment_performance.py -q` → 9 passed
- [ ] `uv run pytest app/tests -k "investment or portfolio" -q` → 0 failed
- [ ] `uv run ruff check` and `uv run ruff format --check` pass on every touched file under `app/`
- [ ] `grep -n "\"get_portfolio_performance\"" app/app/utilities/investment_telemetry.py app/tests/api/test_investment_telemetry.py` → 2 matches
- [ ] `grep -c "portfolio/performance" ../INVESTMENTS_API.md` → ≥ 2
- [ ] `git status --porcelain` shows only in-scope files and anything under `plans/`
- [ ] `plans/README.md` row for 002 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Plan 001 is not DONE, or any symbol under "From plan 001" is missing or has a different signature.
- `PortfolioPerformance` / `PerformanceDataPoint` are referenced anywhere other than `schemas/portfolio.py` and `schemas/__init__.py`.
- A math table row (Step 2) cannot pass without changing the formula, or you believe a row is wrong.
- API case 2 (live = summary) fails: plan 001's totals diverged; do not patch around it here.
- SPLIT transactions turn out not to store the ratio in `quantity`, or `created_at` is nullable/unset in practice.
- A step seems to require editing plan 001's files or an existing route.

## Maintenance notes

- **Known approximations**:
  - Units bought during a day earn no gain until the next snapshot.
  - A position sold in full loses the price move between its last snapshot and the sale.
  - The sale price of a partial sell is assumed to equal the next snapshot's unit value.
  - Dividends are not counted as return.
  - Splits are detected only via SPLIT transactions. A split entered by editing quantity with `PUT /holdings` shows as one day with a large loss.
  - All of these are daily-granularity effects and are documented in `INVESTMENTS_STRUCTURE.md`.
- If a realized-gains or dividends feature is added later, dividends can enter
  `market_gain` as income on the day they are recorded. That is a small change
  confined to `portfolio_performance.py`.
- Charts for `ALL` return one point per day without downsampling (≈365/year). Add
  bucketing only if payload size becomes a problem.
- Reviewers:
  - Every query filters by `current_user.id`.
  - Rounding happens only at the response boundary.
  - The math table in Step 2 is untouched apart from additions.
- Deferred: per-account and per-holding performance, benchmark comparison (S&P 500 / IPC), backfill.
