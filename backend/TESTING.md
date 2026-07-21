# Testing

This document explains how to run and work with the backend test suite.

## Prerequisites

- [Docker](https://www.docker.com/) and [Docker Compose](https://docs.docker.com/compose/install/)
- [uv](https://github.com/astral-sh/uv) for Python package and environment management

## Quick Start

```bash
# 1. Start the test database
docker compose -f docker-compose.test.yml up -d

# 2. Run all tests
cd backend
uv run pytest

# 3. Stop the test database when done
docker compose -f docker-compose.test.yml down
```

## Test Database

Tests run against a dedicated PostgreSQL 16 instance, separate from the development database, to avoid any data contamination.

| Setting  | Value                          |
|----------|--------------------------------|
| Host     | `localhost`                    |
| Port     | `5433` (not the default 5432)  |
| User     | `test_user`                    |
| Password | `test_password`                |
| Database | `test_toston`                  |

The test database uses `tmpfs` for storage, so all data lives in memory and is discarded when the container stops. This makes tests faster and guarantees a clean state on every restart.

**Starting the test database:**

```bash
# From the project root
docker compose -f docker-compose.test.yml up -d

# Verify it's healthy
docker compose -f docker-compose.test.yml ps
```

## Running Tests

All commands below should be run from the `backend/` directory.

```bash
# Run the full suite
uv run pytest

# Run with verbose output
uv run pytest -v

# Run a specific test file
uv run pytest app/tests/api/test_expenses.py

# Run a specific test class
uv run pytest app/tests/crud/test_crud_user.py::TestCRUDUserCreate

# Run a specific test
uv run pytest app/tests/api/test_auth.py::TestLoginAccessToken::test_login_valid_credentials

# Run tests matching a keyword
uv run pytest -k "expense"

# Run with coverage report
uv run pytest --cov=app --cov-report=term-missing

# Run with short tracebacks (useful for fixing multiple failures)
uv run pytest --tb=short

# Stop on first failure
uv run pytest -x
```

## Test Suite Structure

```
backend/app/tests/
├── conftest.py                          # Fixtures: DB engine, sessions, auth, clients
├── utils.py                             # Helpers to create test data directly in DB
├── __init__.py
├── api/                                 # API endpoint tests (HTTP-level)
│   ├── __init__.py
│   ├── test_accounts.py                 # GET/POST/PUT/DELETE /api/v1/accounts
│   ├── test_auth.py                     # Login, token validation, JWT flows
│   ├── test_categories.py               # GET/POST/PUT/DELETE /api/v1/categories
│   ├── test_expenses.py                 # GET/POST/PUT/DELETE /api/v1/expenses
│   ├── test_incomes.py                   # GET/POST/PUT/DELETE /api/v1/incomes
│   ├── test_investment_assets.py         # Asset catalog, search, and prices
│   ├── test_investment_holdings.py       # Position ownership and valuation
│   ├── test_investment_transactions.py   # Ledger and position-changing math
│   ├── test_investment_portfolio.py      # Summary and allocation analytics
│   ├── test_investment_security.py       # Feature gate and security invariants
│   └── test_investment_telemetry.py      # Wide-event retention and redaction
├── crud/                                # CRUD layer tests (database-level)
│   ├── __init__.py
│   ├── test_crud_account.py             # Account CRUD operations
│   ├── test_crud_category.py            # Category CRUD with subcategories
│   ├── test_crud_expense.py             # Expense CRUD with side effects
│   ├── test_crud_income.py              # Income CRUD with side effects
│   ├── test_crud_asset.py                # Investment asset CRUD and search
│   ├── test_crud_asset_price.py          # Price cache ordering and staleness
│   ├── test_crud_holding.py              # Cost basis and valuation CRUD
│   ├── test_crud_investment_transaction.py # Investment ledger CRUD
│   ├── test_crud_transfer.py            # Transfer CRUD with account updates
│   └── test_crud_user.py                # User CRUD, auth, balance updates
└── business_logic/                      # Business rule tests
    ├── __init__.py
    ├── test_balance_updates.py           # Balance propagation across entities
    ├── test_date_filtering.py            # Date-range query behavior
    ├── test_investment_position_math.py  # Multi-transaction position chains
    └── test_investment_valuation_flow.py # Prices, gains, and account totals
```

### Test Layers

- **API tests** (`tests/api/`): Send HTTP requests to FastAPI endpoints via `httpx.AsyncClient` with ASGI transport. These test the full request/response cycle including validation, serialization, and status codes.

- **CRUD tests** (`tests/crud/`): Call CRUD methods directly with an `AsyncSession`. These test database operations and side effects (balance updates, category totals) without going through the HTTP layer.

- **Business logic tests** (`tests/business_logic/`): Test cross-cutting behaviors like balance propagation across users, accounts, and categories, and date-based filtering logic.

## Key Fixtures

Defined in `conftest.py`:

| Fixture                  | Scope    | Description                                                  |
|--------------------------|----------|--------------------------------------------------------------|
| `setup_database`         | session  | Creates all tables at start, drops them at end               |
| `db_session`             | function | Isolated async DB session; rolls back after each test        |
| `test_user`              | function | A regular `User` object created directly in DB               |
| `test_superuser`         | function | A superuser `User` object created directly in DB             |
| `auth_headers`           | function | `{"Authorization": "Bearer <token>"}` for `test_user`       |
| `superuser_auth_headers` | function | `{"Authorization": "Bearer <token>"}` for `test_superuser`  |
| `client`                 | function | `AsyncClient` with DB and auth overrides (authenticated)     |
| `superuser_client`       | function | `AsyncClient` authenticated as `test_superuser`              |
| `enable_investments`     | function | Enables access and patches FX, Redis rate limiting           |
| `unauth_client`          | function | `AsyncClient` with DB override only (no auth, for login tests) |

### Test Isolation

Each test runs inside a database **savepoint** that is rolled back after the test completes. This means:

- Tests never persist data to the database
- Tests cannot affect each other
- No manual cleanup is needed
- Tests can call `commit()` freely; the savepoint is automatically re-created

## Test Utilities

`tests/utils.py` provides helper functions that insert records **directly** into the database, bypassing CRUD side effects (balance updates, category seeding). Use these when setting up test preconditions:

```python
from tests.utils import (
    create_test_user,        # Create a User with hashed password
    create_test_account,     # Create an Account with specified balance
    create_test_category,    # Create a Category
    create_test_subcategory, # Create a Subcategory
    create_test_expense,     # Create an Expense (no balance update)
    create_test_income,      # Create an Income (no balance update)
    create_test_transfer,    # Create a Transfer (no account update)
    create_test_place,       # Create a Place
    create_test_asset,       # Create an investment Asset
    create_test_asset_price, # Create a cached AssetPrice
    create_test_holding,     # Create a Holding with realistic conversions
    create_test_investment_transaction, # Create an immutable ledger row
)
```

### Investment tests

Investment API tests opt into the function-scoped `enable_investments` fixture. It
allowlists `test_user`, fixes USD/MXN at 18, and replaces the three endpoint-local rate
limiter references with async no-ops. Tests that resolve assets or refresh prices patch
the relevant provider or `PriceFetcher` service method, so the suite never calls Yahoo
Finance, CoinGecko, Upstash, or another live service.

## Authentication in Tests

The test suite uses **HS256 JWT tokens** signed with the dev key `"foo"`. The `conftest.py` provides a `create_test_token()` helper:

```python
from tests.conftest import create_test_token

token = create_test_token(user)  # Returns a valid HS256 JWT string
```

- The `client` fixture overrides both `get_current_user` and `get_current_active_user` dependencies, so API tests don't need to deal with tokens at all.
- The `unauth_client` fixture only overrides the DB dependency, leaving auth intact. Use it to test login endpoints and token validation.

## Writing New Tests

### API test example

```python
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.utils import create_test_account


class TestMyFeature:
    async def test_something(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        # Arrange: create test data
        account = await create_test_account(db_session, owner_id=test_user.id)

        # Act: call the endpoint
        resp = await client.get(f"/api/v1/accounts/{account.id}")

        # Assert
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Account"
```

### CRUD test example

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app import crud
from app.schemas.expense import ExpenseCreate
from tests.utils import create_test_user, create_test_account


class TestExpenseCRUD:
    async def test_create_expense_updates_balance(self, db_session: AsyncSession):
        # Arrange
        user = await create_test_user(db_session, email="test@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )

        # Act
        expense_in = ExpenseCreate(amount=250.0, date="2025-01-15", account_id=account.id)
        await crud.expense.create_with_owner(db_session, obj_in=expense_in, owner_id=user.id)

        # Assert
        updated = await crud.account.get_by_id(db_session, owner_id=user.id, id=account.id)
        assert updated.current_balance == 750.0
```

### Guidelines

- Use **unique email addresses** per test to avoid conflicts (e.g. `email="my_test@example.com"`).
- Use `create_test_*` helpers from `tests/utils.py` for setup data. Only use `crud.*` methods when you're testing the CRUD layer itself.
- All test functions must be `async` (pytest-asyncio auto mode handles the event loop).
- No need to decorate tests with `@pytest.mark.asyncio` -- the `asyncio_mode = "auto"` setting in `pyproject.toml` handles this.

## Configuration

Test settings are defined in `backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["app"]
testpaths = ["app/tests"]
python_files = "test_*.py"
python_functions = "test_*"
asyncio_default_fixture_loop_scope = "function"
filterwarnings = [
    "ignore::DeprecationWarning",
]
```

## Troubleshooting

**Tests fail with connection errors:**
Make sure the test database container is running:
```bash
docker compose -f docker-compose.test.yml up -d
```

**Stale data causing unexpected failures:**
Restart the test database container to get a fresh database:
```bash
docker compose -f docker-compose.test.yml down
docker compose -f docker-compose.test.yml up -d
```

**`ModuleNotFoundError` for `app` or `tests`:**
Make sure you're running pytest from the `backend/` directory. The `pythonpath = ["app"]` setting in `pyproject.toml` puts `backend/app/` on `sys.path`.
