# Plan 001: Record a daily portfolio snapshot (with per-holding positions) per user, kept fresh by a background worker

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**, from the repo root:
> `git diff --stat d0e79be..HEAD -- backend/app/app/models backend/app/app/crud/__init__.py backend/app/app/db/base.py backend/app/app/core/config.py backend/app/app/services/price_fetcher.py backend/app/app/api/api_v1/endpoints/portfolio.py backend/app/app/worker backend/app/alembic/versions docker-compose.yml docker-compose.override.yml INVESTMENTS_STRUCTURE.md`
> If any of these files changed since this plan was written, compare the
> "Current state" excerpts below against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (two new tables + migration + new long-running process)
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `d0e79be`, 2026-10-01 (revised same day after a cold review)

## Why this matters

The product needs a "portfolio value over time" chart with an honest return
figure (plan 002). Today that is impossible: the only valuation data is the
*current* value cached on each `Holding`, and asset prices refresh only when a
user calls `POST /api/v1/investments/assets/refresh-prices`. Nothing runs on a
schedule, so there is no history and cached values go stale between visits.

After this plan lands, a new worker process refreshes prices for every held
asset on an interval and upserts, per user per calendar day:

- one `portfolio_snapshot` row (totals, raw cost basis per cost currency, the
  USD/MXN rate used, and when it was captured), and
- one `portfolio_snapshot_holding` row per holding (quantity and value in USD
  and MXN).

The per-holding rows are what let plan 002 separate **market gains** (price and
currency moves on units held across two days) from **money moved in or out**
(buys, sells, deletes, edits, transfers). Totals alone cannot tell those apart.
History starts accumulating from the deploy date (no backfill).

## Current state

Paths are relative to the repo root. The Python package root is
`backend/app/app/` (imported as `app.*`); tests live in `backend/app/tests/`
(imported as `tests.*`). SQLAlchemy is **1.4.54**, Pydantic **2.13**.

- `backend/app/app/worker/export_worker.py` — the only existing background
  worker, run as `python -m app.worker.export_worker`. **Exemplar for the new
  worker; match its structure.** Key shape:

  ```python
  # backend/app/app/worker/export_worker.py:1-22
  """Background worker that builds user data exports and uploads them to R2."""

  from __future__ import annotations

  import asyncio
  import logging
  import os
  import sys

  from app.core.config import settings
  ...
  from app.db.session import async_session
  ...
  logging.basicConfig(level=logging.INFO)
  logger = logging.getLogger("export_worker")

  # backend/app/app/worker/export_worker.py:64-88 (main() also has an R2
  # check that is export-specific — do NOT copy it)
  async def run_forever() -> None:
      logger.info("Export worker started")
      while True:
          try:
              did_work = await tick()
              if not did_work:
                  await asyncio.sleep(IDLE_SLEEP_SECONDS)
          except Exception:
              logger.exception("Export worker loop error")
              await asyncio.sleep(ERROR_SLEEP_SECONDS)


  def main() -> None:
      ...
      try:
          asyncio.run(run_forever())
      except KeyboardInterrupt:
          logger.info("Export worker stopped")
          os._exit(0)
  ```

- `docker-compose.yml:81-94` — the `export-worker` service; copy it for the new worker:

  ```yaml
    export-worker:
      image: "${DOCKER_IMAGE_BACKEND?Variable not set}:${TAG-latest}"
      restart: always
      depends_on:
        db:
          condition: service_healthy
        prestart:
          condition: service_completed_successfully
      env_file:
        - .env
      build:
        context: ./backend
        dockerfile: backend.dockerfile
      command: python -m app.worker.export_worker
  ```

  `docker-compose.override.yml:20-40` overrides `export-worker` for local dev
  (volume mount + `watchfiles` auto-reload). Copy that block too.
  `backend/scripts/prestart.sh` runs `alembic upgrade head` on deploy, so the
  migration applies automatically.

- `backend/app/app/services/price_fetcher.py` — `PriceFetcher.fetch_and_store_price(db, asset)`
  (line 33) fetches one asset's price, appends an `AssetPrice` row, updates
  **every user's** holdings of that asset (`SELECT ... FOR UPDATE`) plus their
  account totals, and commits — atomically. It returns `None` for assets that
  cannot be auto-priced (bonds, CETES, mutual funds; lines 81-84), and on error
  calls `db.rollback()` and re-raises. Do **not** use
  `PriceFetcher.refresh_all_prices(db, owner_id=None)` (line 148): it refreshes up
  to 1000 active assets whether held or not. Do not modify `price_fetcher.py`.

- `backend/app/app/crud/crud_asset_price.py:67` — `asset_price.is_stale(db, *, asset_id, max_age_minutes)`.
- `crud.asset.get(db, id=...)` — generic `CRUDBase.get`; returns `None` if missing.
- `backend/app/app/services/currency_converter.py:35` — `CurrencyConverter.get_usd_to_mxn_rate()`
  returns a `Decimal` or raises `CurrencyRateUnavailable` (no hard-coded fallback,
  despite `INVESTMENTS_STRUCTURE.md`).

- `backend/app/app/api/api_v1/endpoints/portfolio.py:74-89` — the summary math.
  **Snapshot totals must match it exactly**:

  ```python
  for holding in holdings:
      total_value_usd += holding.current_value_usd
      total_value_mxn += holding.current_value_mxn
      # Separate total_invested by cost_currency
      if holding.cost_currency == Currency.USD:
          total_invested_usd += holding.total_invested
      else:
          total_invested_mxn += holding.total_invested

  # Calculate combined totals (all investments converted to single currency)
  total_invested_combined_usd = total_invested_usd + (
      total_invested_mxn / usd_mxn_rate
  )
  total_invested_combined_mxn = (
      total_invested_usd * usd_mxn_rate
  ) + total_invested_mxn
  ```

  **Naming rule (important):** in the summary, `total_invested_usd` /
  `total_invested_mxn` mean the **raw** cost basis of holdings whose
  `cost_currency` is USD / MXN, unconverted. `total_invested_combined_*` is the
  converted grand total. Use exactly the same names with the same meanings
  everywhere in this plan.

  Holdings for one user: `crud.holding.get_by_owner(db, owner_id=..., limit=None)`
  (`crud_holding.py:129`, selectin-loads `asset`; the totals only read scalar
  columns, so no lazy-load issues).

- Model exemplar: `backend/app/app/models/data_export.py` (user-owned table,
  `ForeignKey("user.id", ondelete="CASCADE")`). Money columns use `Numeric(38, 8)`,
  quantities `Numeric(28, 12)`, rates `Numeric(20, 12)` (see `models/holding.py:66`,
  `models/asset_price.py`, `models/investment_transaction.py:101`). Date-only
  columns use `Column(Date, ...)` (`models/balance_adjustment.py:30`).

- Model registration: new models must be imported in **both**
  `backend/app/app/models/__init__.py` and `backend/app/app/db/base.py` (Alembic
  and the test suite's `Base.metadata.create_all` read the latter).

- CRUD exemplar: `backend/app/app/crud/crud_data_export.py` — a plain class with
  async methods (`db: AsyncSession`, keyword-only args), a module-level singleton,
  re-exported from `backend/app/app/crud/__init__.py`.

- Migration exemplar: `backend/app/alembic/versions/c3e8a91b4d20_add_data_export.py`,
  the **current single Alembic head**. Style: inspector guard, explicit
  `op.create_index`, symmetric `downgrade`.

- Settings: `backend/app/app/core/config.py:103-106`:

  ```python
  # Investment feature access
  INVESTMENTS_ENABLED: bool = False
  INVESTMENTS_ALLOWED_USER_IDS: str = ""
  INVESTMENTS_ALLOWED_USER_UUIDS: str = ""
  ```

- User deletion (`crud.user.remove`) is an ORM delete with no relationship to the
  new tables; DB-level `ondelete="CASCADE"` removes snapshots. Do **not** add a
  relationship on `User`.

- Test DB session (`backend/app/tests/conftest.py:131-157`): `db_session` is bound
  to a connection with an outer transaction and a nested savepoint that is
  re-created after each commit. **Never let code under test call
  `db.rollback()` on `db_session`** — it can discard the test's data. This is why
  the worker tests below mock `PriceFetcher.fetch_and_store_price`.

### Conventions

- Commit style: `feat: add exports with worker base`, `fix: alembic dataexportstatus`, `tests: add investment tests`.
- Decimal math for money; never convert to `float` in services.
- Ruff config is in `backend/pyproject.toml`. The repo has ~349 pre-existing ruff
  errors, so lint **only files you create or modify**. mypy is not a gate.

## Commands you will need

Run from `backend/` unless noted.

| Purpose | Command | Expected on success |
|---|---|---|
| Start test DB (repo root) | `docker compose -f docker-compose.test.yml up -d` | `db-test` healthy on port 5433 |
| Install deps | `uv sync` | exit 0 |
| New tests | `uv run pytest app/tests/services/test_portfolio_snapshot.py -v` | all pass |
| Investments regression | `uv run pytest app/tests -k "investment or portfolio" -q` | 0 failed |
| Lint touched files | `uv run ruff check <files>` | `All checks passed!` |
| Format check | `uv run ruff format --check <files>` | `already formatted` |
| Alembic heads (from `backend/app/`) | `uv run alembic heads` | one line: `a7d41f0c9e52 (head)` |

Before changing anything, run the investments regression command and record the
pass count. If it fails on the untouched tree, STOP.

## Scope

**In scope** (the only files you may create or modify):
- `backend/app/app/models/portfolio_snapshot.py` (create; holds both models)
- `backend/app/app/models/__init__.py`, `backend/app/app/db/base.py` (add imports)
- `backend/app/app/crud/crud_portfolio_snapshot.py` (create), `backend/app/app/crud/__init__.py` (add export)
- `backend/app/app/services/portfolio_snapshot.py` (create)
- `backend/app/app/worker/portfolio_snapshot_worker.py` (create)
- `backend/app/app/core/config.py` (three settings)
- `backend/app/alembic/versions/a7d41f0c9e52_add_portfolio_snapshot.py` (create)
- `backend/app/tests/services/test_portfolio_snapshot.py` (create)
- `docker-compose.yml`, `docker-compose.override.yml` (add service)
- `INVESTMENTS_STRUCTURE.md` (docs)
- `plans/README.md` (status row only)

**Out of scope** (do NOT touch):
- `backend/app/app/services/price_fetcher.py` — shared with user-facing routes.
- `backend/app/app/api/api_v1/endpoints/portfolio.py` — do not refactor the summary to use the new helper; test case 3 pins parity instead. Plan 002 adds a route here.
- Any HTTP route (plan 002 adds the only one).
- `backend/app/app/utilities/investment_telemetry.py` — request-scoped; the worker uses `logging`.
- `.env`, `.env.develop`, `services/user_export.py`.

## Git workflow

- Branch: `advisor/001-portfolio-snapshots` off `develop`.
- Conventional commits, e.g. `feat: add portfolio snapshot models and migration`.
- Do NOT push or open a PR unless instructed.

## Steps

### Step 1: Add the settings

In `backend/app/app/core/config.py`, directly after line 106
(`INVESTMENTS_ALLOWED_USER_UUIDS: str = ""`), add:

```python
    # Portfolio snapshot worker
    PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS: int = 3600
    PORTFOLIO_SNAPSHOT_PRICE_MAX_AGE_MINUTES: int = 60
    PORTFOLIO_SNAPSHOT_TIMEZONE: str = "America/Mexico_City"
```

**Verify**: `grep -n "PORTFOLIO_SNAPSHOT" app/app/core/config.py` → 3 lines.

### Step 2: Create the models and register them

Create `backend/app/app/models/portfolio_snapshot.py`:

```python
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base


class PortfolioSnapshot(Base):
    """
    One valuation of a user's whole portfolio per calendar day.

    Upserted by the portfolio snapshot worker; the last run of the day wins.
    total_invested_usd / total_invested_mxn are the RAW cost basis of holdings
    whose cost currency is USD / MXN (same meaning as /portfolio/summary).
    """

    __tablename__ = "portfolio_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "snapshot_date", name="uq_portfolio_snapshot_owner_date"
        ),
        CheckConstraint(
            "total_value_usd >= 0 AND total_value_mxn >= 0",
            name="ck_portfolio_snapshot_value_nonnegative",
        ),
        CheckConstraint(
            "total_invested_usd >= 0 AND total_invested_mxn >= 0",
            name="ck_portfolio_snapshot_invested_nonnegative",
        ),
        CheckConstraint("usd_mxn_rate > 0", name="ck_portfolio_snapshot_rate_positive"),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_date: date = Column(Date, nullable=False)
    # When the values were last written; plan 002 uses it to place splits
    # between two snapshots.
    captured_at: datetime = Column(DateTime(timezone=True), nullable=False)

    total_value_usd: Decimal = Column(Numeric(38, 8), nullable=False)
    total_value_mxn: Decimal = Column(Numeric(38, 8), nullable=False)
    total_invested_usd: Decimal = Column(Numeric(38, 8), nullable=False)
    total_invested_mxn: Decimal = Column(Numeric(38, 8), nullable=False)
    usd_mxn_rate: Decimal = Column(Numeric(20, 12), nullable=False)
    holdings_count: int = Column(Integer, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    positions: list["PortfolioSnapshotHolding"] = relationship(
        "PortfolioSnapshotHolding",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PortfolioSnapshotHolding(Base):
    """One holding's position inside a PortfolioSnapshot."""

    __tablename__ = "portfolio_snapshot_holding"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "holding_id", name="uq_portfolio_snapshot_holding"
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    snapshot_id: int = Column(
        Integer,
        ForeignKey("portfolio_snapshot.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # No FK: holdings can be deleted while their history must remain.
    holding_id: int = Column(Integer, nullable=False)
    asset_id: int = Column(Integer, nullable=False)
    quantity: Decimal = Column(Numeric(28, 12), nullable=False)
    value_usd: Decimal = Column(Numeric(38, 8), nullable=False)
    value_mxn: Decimal = Column(Numeric(38, 8), nullable=False)
```

Register both:
- `models/__init__.py`: `from .portfolio_snapshot import PortfolioSnapshot, PortfolioSnapshotHolding`
  (after the `.place` import, before `.subcategory`).
- `db/base.py`: `from app.models.portfolio_snapshot import PortfolioSnapshot, PortfolioSnapshotHolding  # noqa`
  (after the `item` import, before `user`).

**Verify**: `uv run ruff check app/app/models/portfolio_snapshot.py app/app/models/__init__.py app/app/db/base.py` → `All checks passed!`

### Step 3: Write the migration

Create `backend/app/alembic/versions/a7d41f0c9e52_add_portfolio_snapshot.py`,
following `c3e8a91b4d20_add_data_export.py`:

- `revision = "a7d41f0c9e52"`, `down_revision = "c3e8a91b4d20"`.
- `upgrade()`: inspector guard on `"portfolio_snapshot"`. Create
  `portfolio_snapshot` with every column, type and nullability from Step 2,
  `server_default=sa.func.now()` on `created_at`, FK to `user.id` with
  `ondelete="CASCADE"`, PK, the unique constraint and the three named check
  constraints (`sa.UniqueConstraint(..., name=...)` / `sa.CheckConstraint(..., name=...)`
  inside `create_table`). Then create `portfolio_snapshot_holding` with its
  columns, PK, FK `snapshot_id → portfolio_snapshot.id` `ondelete="CASCADE"`,
  and `uq_portfolio_snapshot_holding`. Then indexes:
  `ix_portfolio_snapshot_id`, `ix_portfolio_snapshot_owner_id`,
  `ix_portfolio_snapshot_holding_id`, `ix_portfolio_snapshot_holding_snapshot_id`.
- `downgrade()`: guarded; drop the four indexes, then `portfolio_snapshot_holding`,
  then `portfolio_snapshot`.

Do not run `alembic upgrade` against any database yourself.

**Verify** (from `backend/app/`): `uv run alembic heads` → exactly `a7d41f0c9e52 (head)`.

### Step 4: Create the snapshot service (pure parts first)

Create `backend/app/app/services/portfolio_snapshot.py` with:

```python
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionValue:
    holding_id: int
    asset_id: int
    quantity: Decimal
    value_usd: Decimal
    value_mxn: Decimal


@dataclass(frozen=True)
class PortfolioTotals:
    total_value_usd: Decimal
    total_value_mxn: Decimal
    total_invested_usd: Decimal            # raw, USD-cost holdings only
    total_invested_mxn: Decimal            # raw, MXN-cost holdings only
    total_invested_combined_usd: Decimal   # converted grand total
    total_invested_combined_mxn: Decimal   # converted grand total
    holdings_count: int
    positions: tuple[PositionValue, ...]


def compute_portfolio_totals(
    holdings: Iterable[Holding], usd_mxn_rate: Decimal
) -> PortfolioTotals: ...


def combined_invested(
    total_invested_usd: Decimal,
    total_invested_mxn: Decimal,
    usd_mxn_rate: Decimal,
    currency: Currency,
) -> Decimal:
    """Combined cost basis in `currency`, using the summary's conversion."""


def portfolio_today() -> date:
    """Calendar date in PORTFOLIO_SNAPSHOT_TIMEZONE; the key for snapshot rows."""
    return datetime.now(ZoneInfo(settings.PORTFOLIO_SNAPSHOT_TIMEZONE)).date()
```

Rules:
- `compute_portfolio_totals` reproduces `portfolio.py:74-89` **exactly** (no
  rounding) and builds one `PositionValue` per holding with
  `quantity > 0` from `holding.id`, `holding.asset_id`, `holding.quantity`,
  `holding.current_value_usd`, `holding.current_value_mxn`. `holdings_count` is
  the number of holdings passed in (same as the summary's `total_holdings`).
- `combined_invested` returns `total_invested_usd + total_invested_mxn / rate`
  for USD and `total_invested_usd * rate + total_invested_mxn` for MXN. Plan 002
  uses it for both stored snapshots (with their stored rate) and the live point.

**Verify**: `uv run ruff check app/app/services/portfolio_snapshot.py` → passes.

### Step 5: Create the CRUD module

Create `backend/app/app/crud/crud_portfolio_snapshot.py` with class
`CRUDPortfolioSnapshot` and singleton `portfolio_snapshot`:

- `async def upsert(self, db, *, owner_id: int, snapshot_date: date, totals: "PortfolioTotals", usd_mxn_rate: Decimal) -> int`
  1. Parent upsert with `from sqlalchemy.dialects.postgresql import insert`:
     `insert(PortfolioSnapshot).values(owner_id=..., snapshot_date=..., captured_at=func.now(), <totals columns>, usd_mxn_rate=..., holdings_count=...)`
     `.on_conflict_do_update(constraint="uq_portfolio_snapshot_owner_date", set_={<every value column>, "captured_at": func.now(), "updated_at": func.now()})`
     `.returning(PortfolioSnapshot.id)`; `snapshot_id = (await db.execute(stmt)).scalar_one()`.
     (`updated_at` must be set explicitly: ORM `onupdate` does not fire on ON CONFLICT.)
  2. `await db.execute(delete(PortfolioSnapshotHolding).where(PortfolioSnapshotHolding.snapshot_id == snapshot_id))`.
  3. If `totals.positions` is non-empty, bulk insert one `PortfolioSnapshotHolding`
     row per position (`await db.execute(insert(PortfolioSnapshotHolding), [dicts])`).
  4. Return `snapshot_id`. **Do not commit.** Import `PortfolioTotals` under
     `if TYPE_CHECKING:` to avoid an import cycle.
- `async def get_range(self, db, *, owner_id: int, start_date: date | None) -> list[PortfolioSnapshot]`
  — rows with `snapshot_date >= start_date` (no filter when `None`), ascending by
  `snapshot_date`, with `.options(selectinload(PortfolioSnapshot.positions))` and
  `.execution_options(populate_existing=True)` (the Core upsert bypasses the
  identity map, so already-loaded objects would otherwise be stale).
- `async def get_latest_on_or_before(self, db, *, owner_id: int, day: date) -> PortfolioSnapshot | None`
  — the row with the greatest `snapshot_date <= day`, same options as above.

Add `from .crud_portfolio_snapshot import portfolio_snapshot` to `crud/__init__.py`.

**Verify**: `uv run ruff check app/app/crud/crud_portfolio_snapshot.py app/app/crud/__init__.py` → passes.

### Step 6: Add the DB-facing service functions

Append to `backend/app/app/services/portfolio_snapshot.py`:

```python
async def snapshot_owner(
    db: AsyncSession, *, owner_id: int, snapshot_date: date, usd_mxn_rate: Decimal
) -> PortfolioTotals:
    """Load the owner's holdings and upsert today's snapshot. Does not commit."""


async def snapshot_all_owners(
    db: AsyncSession, *, snapshot_date: date, usd_mxn_rate: Decimal
) -> int:
    """Snapshot every owner with at least one holding; returns successes. Does not commit."""


async def held_active_asset_ids(db: AsyncSession) -> list[int]:
    """Distinct asset ids with Holding.quantity > 0 and Asset.is_active true."""
```

`snapshot_all_owners` must isolate failures per owner with a savepoint, so
one bad owner cannot drop everyone's snapshot:

```python
owner_ids = (await db.execute(select(Holding.owner_id).distinct())).scalars().all()
count = 0
for owner_id in owner_ids:
    try:
        async with db.begin_nested():
            await snapshot_owner(
                db, owner_id=owner_id, snapshot_date=snapshot_date,
                usd_mxn_rate=usd_mxn_rate,
            )
        count += 1
    except Exception:
        logger.exception("Portfolio snapshot failed for owner %s", owner_id)
return count
```

Note: this snapshots every user who has holdings, regardless of
`INVESTMENTS_ALLOWED_USER_IDS`. That is intended (holdings exist only for users
who had access); the API in plan 002 is still gated.

**Verify**: `uv run ruff check app/app/services/portfolio_snapshot.py` → passes.

### Step 7: Create the worker

Create `backend/app/app/worker/portfolio_snapshot_worker.py` mirroring
`export_worker.py` (docstring, `from __future__ import annotations`,
`logging.basicConfig(level=logging.INFO)`,
`logger = logging.getLogger("portfolio_snapshot_worker")`, `tick`,
`run_forever`, `main`, `if __name__ == "__main__": main()`).

`async def tick() -> None`:
1. If `not settings.INVESTMENTS_ENABLED`: `logger.debug(...)` and return. (The
   container then idles on every interval — expected when the feature is off.)
2. Prices: `async with async_session() as db:` →
   `asset_ids = await held_active_asset_ids(db)`. For each id, open a **fresh**
   `async with async_session() as db:` and:
   - `asset = await crud.asset.get(db, id=asset_id)`; if `None`, continue;
   - if not `await crud.asset_price.is_stale(db, asset_id=asset_id, max_age_minutes=settings.PORTFOLIO_SNAPSHOT_PRICE_MAX_AGE_MINUTES)`, continue;
   - `await PriceFetcher.fetch_and_store_price(db, asset)` inside
     `try/except Exception` → `logger.exception(...)`, continue. A `None`
     result means "not auto-priceable": `logger.debug`, not an error.
3. FX: `rate = await CurrencyConverter.get_usd_to_mxn_rate()`; on
   `CurrencyRateUnavailable`: `logger.warning(...)` and **return without writing
   snapshots** (a guessed rate would corrupt history).
4. `async with async_session() as db:` →
   `count = await snapshot_all_owners(db, snapshot_date=portfolio_today(), usd_mxn_rate=rate)`;
   `await db.commit()`; `logger.info("Snapshotted %d portfolios", count)`.

`run_forever()`: loop forever: `await tick()` inside `try/except Exception`
(`logger.exception`), then `await asyncio.sleep(settings.PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS)`.

`main()`: `asyncio.run(run_forever())` with `export_worker.main`'s
`KeyboardInterrupt` handling. No R2 check.

**Verify**: `uv run ruff check app/app/worker/portfolio_snapshot_worker.py` passes, and
`uv run ruff format --check` passes on every file you created (run `uv run ruff format <file>` on **your new files only** if needed).

### Step 8: Add the compose service

- `docker-compose.yml`: add `portfolio-worker` right after `export-worker`,
  identical except `command: python -m app.worker.portfolio_snapshot_worker`.
- `docker-compose.override.yml`: add `portfolio-worker` identical to the
  `export-worker` block (lines 20-40) except the module name in `command`.

**Verify** (repo root): `grep -n "portfolio_snapshot_worker" docker-compose.yml docker-compose.override.yml` → 2 lines.
(`docker compose config` also works but prints unrelated interpolation warnings from `.env`; ignore them.)

### Step 9: Document it

In `INVESTMENTS_STRUCTURE.md`:
- "System Map" tree: add `portfolio_snapshot.py` under `models/` and `services/`,
  `crud_portfolio_snapshot.py` under `crud/`, and a `worker/` entry with
  `portfolio_snapshot_worker.py`.
- Under "Core Workflows" add `### Daily portfolio snapshots` (6–10 lines): interval
  setting; refreshes stale prices of held active assets (each refresh locks all
  holdings of that asset briefly, same lock order as transaction routes); writes
  nothing without a trusted USD/MXN rate; upserts one snapshot plus per-holding
  positions per user per day keyed by `PORTFOLIO_SNAPSHOT_TIMEZONE`; history
  begins on deploy day; run a single replica.
- "Current Limitations": rewrite item 1 (money already uses `Numeric`/`Decimal`),
  item 4 (buy fees **are** included in cost basis — `crud_investment_transaction.py:63`),
  and item 9 (there is no `17.0` fallback; requests fail with 503 when no rate
  is available). These are stale today; fix them while you are here.

**Verify**: `grep -c "portfolio_snapshot" ../INVESTMENTS_STRUCTURE.md` → ≥ 3, and `grep -n "17.0" ../INVESTMENTS_STRUCTURE.md` → no match.

## Test plan

Create `backend/app/tests/services/test_portfolio_snapshot.py`. Model setup on
`backend/app/tests/api/test_investment_portfolio.py:19-58` (`_portfolio`: two
accounts, a stock and a crypto asset via `create_test_asset`,
`create_test_holding`, then `current_value*` set directly and committed). Header
`# ruff: noqa: ARG001`; `asyncio_mode = "auto"` is configured. Fixtures:
`db_session`, `test_user`, `client`, `enable_investments` (sets
`INVESTMENTS_ENABLED`, allowlists `test_user`, mocks the rate to `Decimal("18")`).

For worker tests, use exactly this session fake (it must **not** close the
shared session):

```python
from contextlib import asynccontextmanager


def _fake_session_factory(db_session):
    @asynccontextmanager
    async def _session():
        yield db_session

    return _session

# in the test:
monkeypatch.setattr(
    "app.worker.portfolio_snapshot_worker.async_session",
    _fake_session_factory(db_session),
)
```

**Always** patch `PriceFetcher.fetch_and_store_price` in worker tests
(`AsyncMock(return_value=None)`): the real one calls Yahoo/CoinGecko and may call
`db.rollback()`, which can wipe the test's data.

Cases:
1. `compute_portfolio_totals([])` → all zeros, `holdings_count == 0`, `positions == ()`.
2. `compute_portfolio_totals` with one USD-cost and one MXN-cost holding (rate
   `Decimal("18")`) → raw and combined invested match hand-computed values
   exactly; a zero-quantity holding is excluded from `positions`.
3. **Parity with the summary endpoint**: build the exemplar portfolio **plus one
   MXN-cost holding** (`create_test_holding(..., cost_currency=Currency.MXN, avg_cost_basis=Decimal("1800"))`),
   call `GET /api/v1/investments/portfolio/summary`, and assert
   `compute_portfolio_totals(holdings, Decimal("18"))` rounded to 2 places equals
   the response's `total_value_usd`, `total_value_mxn`, `total_invested_usd`,
   `total_invested_mxn`, `total_invested_combined_usd`, `total_invested_combined_mxn`.
4. **Upsert replaces**: call `snapshot_owner` twice for the same date, changing a
   holding's `current_value_usd` in between. **Only after both calls**, read with
   `crud.portfolio_snapshot.get_range` → exactly one row, holding the second
   values, and its `positions` reflect the second values (no duplicates).
5. `snapshot_all_owners` with two users who have holdings and one who has none →
   returns 2; the third has no row.
6. **Per-owner isolation**: monkeypatch `app.services.portfolio_snapshot.snapshot_owner`
   with a wrapper that raises `RuntimeError` for one owner id and calls the real
   function otherwise → returns 1, and the other owner's row exists.
7. `get_range` ascending + `start_date` filter; `get_latest_on_or_before` returns
   the right row and `None` when nothing qualifies.
8. `held_active_asset_ids` excludes zero-quantity holdings and inactive assets.
9. **Worker skips snapshots without FX**: one holding for `test_user`;
   `enable_investments`; patch `CurrencyConverter.get_usd_to_mxn_rate` with
   `AsyncMock(side_effect=CurrencyRateUnavailable("x"))` (after the fixture) and
   `PriceFetcher.fetch_and_store_price` with `AsyncMock(return_value=None)`; fake
   session; `await tick()` → zero snapshot rows **and** the price mock was awaited
   at least once.
10. **Worker writes a snapshot**: same setup but FX left at 18 →
    one snapshot for `portfolio_today()` with one position row; price mock awaited.

**Verify**: `uv run pytest app/tests/services/test_portfolio_snapshot.py -v` → 10 passed.

## Done criteria

All from `backend/` unless noted:

- [ ] `uv run pytest app/tests/services/test_portfolio_snapshot.py -q` → 10 passed
- [ ] `uv run pytest app/tests -k "investment or portfolio" -q` → baseline + new tests, 0 failed
- [ ] `uv run ruff check` and `uv run ruff format --check` pass on every created/modified file under `app/`
- [ ] (from `backend/app/`) `uv run alembic heads` → single head `a7d41f0c9e52`
- [ ] `grep -n "portfolio_snapshot_worker" ../docker-compose.yml ../docker-compose.override.yml` → 2 lines
- [ ] `git status --porcelain` shows only in-scope files **and anything under `plans/`** (plan files may already be staged; that is fine)
- [ ] `plans/README.md` row for 001 updated

## STOP conditions

Stop and report back (do not improvise) if:

- The investments regression fails **before** you change anything.
- `uv run alembic heads` shows a head other than `c3e8a91b4d20` before Step 3.
- The summary math at `portfolio.py:74-89` or `crud.holding.get_by_owner` no longer matches the excerpts.
- `PriceFetcher.fetch_and_store_price` no longer commits internally, or its signature differs.
- Test case 3 (parity) fails and fixing it seems to need changes to `portfolio.py`.
- The upsert (`on_conflict_do_update(...).returning(...)`) or `begin_nested` fails under the test fixture after two fix attempts.

## Maintenance notes

- **Plan 002** depends on: `PortfolioSnapshot` / `PortfolioSnapshotHolding` columns
  (incl. `captured_at`), `crud.portfolio_snapshot.get_range` /
  `get_latest_on_or_before`, `compute_portfolio_totals`, `combined_invested`,
  `PositionValue`, `portfolio_today`. Keep them stable or update 002.
- If the summary math changes, update `compute_portfolio_totals` in the same PR;
  test case 3 fails if they diverge. A later refactor can make the summary call it.
- Snapshot dates use one server-wide timezone. Revisit (per-user timezone) if many
  users live outside Mexico.
- Upstream call volume = distinct held assets × runs/day. Watch for 429s as users grow.
- Each price refresh `SELECT ... FOR UPDATE`s every holding of that asset, then
  account rows — same order as the transaction routes, so no deadlock cycle, but
  brief contention. Fine hourly; reconsider if the interval drops to minutes.
- Run one replica: two workers can refresh the same asset (`is_stale` narrows but
  does not lock).
- Optional: list `portfolio-worker` in the `.env.develop` comment that names the
  services built from the backend image (out of scope here).
- Deferred: backfill from transactions + historical prices; pruning `assetprice`
  (`crud_asset_price.cleanup_old_prices` is still a stub); pruning old snapshot
  position rows if the table grows large.
