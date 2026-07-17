from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# from fastapi.testclient import TestClient
# from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from websockets.client import ClientConnection as Connect

from app import crud
from app.core.config import settings
from app.db import base  # noqa: F401
from app.db.init_db import init_db

# from app.db.session import SessionLocal
from app.db.session import async_session, engine_async
from app.main import app
from app.schemas.user import UserCreate
from app.tests.utils.user import authentication_token_from_email
from app.tests.utils.utils import get_superuser_token_headers


def _assert_safe_test_database() -> None:
    """Fail closed before any destructive database reset."""
    database_name = settings.POSTGRES_DB.strip().lower()
    if not settings.TEST_MODE:
        raise RuntimeError("Refusing to reset database unless TEST_MODE=true")
    if not (database_name.endswith("_test") or database_name.startswith("test_")):
        raise RuntimeError(
            "Refusing to reset a database whose name is not explicitly test-only"
        )


class WsTestClient(Connect):
    base_url = f"ws://localhost{settings.API_V1_STR}"

    def __init__(self, url) -> None:
        super().__init__(self.base_url + url)


@pytest_asyncio.fixture
async def async_get_db() -> AsyncGenerator:
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture(scope="module")
def ws_client() -> type[WsTestClient]:
    return WsTestClient


@pytest_asyncio.fixture
async def client() -> AsyncGenerator:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def superuser_token_headers(client: AsyncClient) -> dict[str, str]:
    headers = await get_superuser_token_headers(client)
    return headers


@pytest_asyncio.fixture
async def normal_user_token_headers(
    client: AsyncClient, async_get_db: AsyncSession
) -> dict[str, str]:
    headers = await authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=async_get_db
    )
    return headers


@pytest_asyncio.fixture(autouse=True)
async def clear_db() -> AsyncGenerator[None, None]:
    try:
        _assert_safe_test_database()
        # Try to create session to check if DB is awake
        async with engine_async.begin() as conn:
            # The model graph contains named and unnamed circular foreign keys, so
            # SQLAlchemy cannot reliably topologically sort metadata.drop_all().
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.run_sync(base.Base.metadata.create_all)
        async with async_session() as db:
            await init_db(db=db)
            superuser = await crud.user.get_by_email(db, email=settings.FIRST_SUPERUSER)
            if not superuser:
                await crud.user.create(
                    db,
                    obj_in=UserCreate(
                        email=settings.FIRST_SUPERUSER,
                        password=settings.FIRST_SUPERUSER_PASSWORD,
                        country="MXN",
                        name="Test Superuser",
                        is_superuser=True,
                    ),
                )
            await db.execute("SELECT 1")
        yield
        await engine_async.dispose()
    except Exception as e:
        raise e
