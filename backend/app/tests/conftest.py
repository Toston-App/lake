"""
Core test configuration and fixtures for the Toston backend test suite.

This module provides:
- Test database engine and session (PostgreSQL on port 5433)
- Automatic table creation/teardown per session
- Per-test transaction isolation via savepoints
- FastAPI test client with dependency overrides
- Authentication fixtures (test user + JWT token)
"""
import os
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# -- Environment variables for test Settings --
# These must be set BEFORE importing anything from `app` so that
# pydantic-settings picks them up instead of requiring a .env file.
os.environ.setdefault("POSTGRES_SERVER", "localhost:5433")
os.environ.setdefault("POSTGRES_USER", "test_user")
os.environ.setdefault("POSTGRES_PASSWORD", "test_password")
os.environ.setdefault("POSTGRES_DB", "test_toston")
os.environ.setdefault("PROJECT_NAME", "Toston Test")
os.environ.setdefault("SERVER_NAME", "localhost")
os.environ.setdefault("SERVER_HOST", "http://localhost")
os.environ.setdefault("FIRST_SUPERUSER", "admin@test.com")
os.environ.setdefault("FIRST_SUPERUSER_PASSWORD", "testpassword")
os.environ.setdefault("ENCRYPTION_KEY", "dGVzdF9lbmNyeXB0aW9uX2tleV8xMjM0NTY3ODk=")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "bot:test-token")
os.environ.setdefault("TELEGRAM_OWNER_ID", "123456")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test-token")
os.environ.setdefault("WAHA_SESSION", "default")
os.environ.setdefault("WAHA_URL", "http://localhost:3000")
os.environ.setdefault("WHATSAPP_API_KEY", "test-key")
os.environ.setdefault("REDIS_URL", "https://fake-redis.upstash.io")
os.environ.setdefault("REDIS_TOKEN", "fake-token")
os.environ.setdefault("DOCS_USER", "admin")
os.environ.setdefault("DOCS_PASSWORD", "password")
# SECRET_KEY must be valid base64 for the security module
os.environ.setdefault("SECRET_KEY", "dGVzdC1wdWJsaWMta2V5LWZvci10ZXN0aW5n")
os.environ.setdefault("PROFILE_QUERY_MODE", "False")
os.environ.setdefault("SENTRY_DSN", "")

from app.db.base_class import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.api.deps import async_get_db, get_current_user, get_current_active_user  # noqa: E402
from app.models.user import User  # noqa: E402
# Import all models so Base.metadata knows about them
from app.models import (  # noqa: E402, F401
    Account,
    BalanceAdjustment,
    Category,
    Subcategory,
    Transfer,
    Income,
    Expense,
    Place,
    Item,
    Import,
)

try:
    from app.models.feedback import Feedback  # noqa: F401
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Test database engine
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "postgresql+asyncpg://test_user:test_password@localhost:5433/test_toston"

test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
TestSessionLocal = sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Session-scoped: create / drop all tables once per test run
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def setup_database():
    """Create all tables at the start of the test session, drop them at the end."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        # Use CASCADE to handle circular foreign key dependencies
        # between account, import, and user tables.
        from sqlalchemy import text
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await test_engine.dispose()


# ---------------------------------------------------------------------------
# Per-test: provide an isolated database session via savepoint rollback
# ---------------------------------------------------------------------------
@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession wrapped in a transaction that is rolled back
    after each test, ensuring complete test isolation.
    """
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        # Use a nested savepoint so the application code can call
        # commit() without actually persisting to the database.
        nested = await connection.begin_nested()

        # Re-create a savepoint every time the application commits
        @event.listens_for(session.sync_session, "after_transaction_end")
        def restart_savepoint(session_sync, transaction_sync):
            nonlocal nested
            if transaction_sync.nested and not transaction_sync._parent.nested:
                nested = connection.sync_connection.begin_nested()

        yield session

        await session.close()
        await transaction.rollback()


# ---------------------------------------------------------------------------
# Test user fixture
# ---------------------------------------------------------------------------
@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create a basic test user directly in the database (skips category seeding)."""
    from app.core.security import get_password_hash

    user = User(
        email="testuser@example.com",
        hashed_password=get_password_hash("testpassword123"),
        name="Test User",
        country="USD",
        is_active=True,
        is_superuser=False,
        balance_total=0.0,
        balance_income=0.0,
        balance_outcome=0.0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def test_superuser(db_session: AsyncSession) -> User:
    """Create a superuser for testing admin-only endpoints."""
    from app.core.security import get_password_hash

    user = User(
        email="admin@example.com",
        hashed_password=get_password_hash("adminpassword123"),
        name="Admin User",
        country="USD",
        is_active=True,
        is_superuser=True,
        balance_total=0.0,
        balance_income=0.0,
        balance_outcome=0.0,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def create_test_token(user: User) -> str:
    """Create a valid HS256 JWT token for the given user (uses the dev 'foo' key)."""
    expire = datetime.utcnow() + timedelta(hours=1)
    payload = {
        "exp": expire,
        "user": {
            "name": user.name,
            "email": user.email,
            "country": user.country,
            "id": user.id,
        },
    }
    return jwt.encode(payload, "foo", algorithm="HS256")


@pytest.fixture
def auth_headers(test_user: User) -> dict[str, str]:
    """Return Authorization headers with a valid JWT for the test user."""
    token = create_test_token(test_user)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def superuser_auth_headers(test_superuser: User) -> dict[str, str]:
    """Return Authorization headers with a valid JWT for the superuser."""
    token = create_test_token(test_superuser)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# FastAPI test client with dependency overrides
# ---------------------------------------------------------------------------
@pytest.fixture
async def client(
    db_session: AsyncSession, test_user: User
) -> AsyncGenerator[AsyncClient, None]:
    """
    Provide an httpx AsyncClient that talks to the FastAPI app
    with overridden dependencies for DB session and auth.
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def _override_get_current_user() -> User:
        return test_user

    app.dependency_overrides[async_get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_current_active_user] = _override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def unauth_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """
    Provide an httpx AsyncClient WITHOUT auth overrides.
    Useful for testing authentication flows.
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[async_get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
