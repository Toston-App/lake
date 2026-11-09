"""
API tests for the recurring parts of /api/v1/demo-data (superuser only).

Generating demo data twice must not duplicate the demo recurring templates, and
clearing a user's financial data must also clear their recurring-detection state.
"""

from unittest.mock import AsyncMock

import pytest
from app.models import (
    RecurringDetectionUsage,
    RecurringMerchantAlias,
    RecurringSuggestionDismissal,
    RecurringTransaction,
    User,
)
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import create_test_category, create_test_subcategory, create_test_user

PREFIX = "/api/v1/demo-data"


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )


@pytest.fixture
async def target_user(db_session: AsyncSession) -> User:
    user = await create_test_user(db_session, email="demo-target@example.com")
    income = await create_test_category(
        db_session, owner_id=user.id, name="Income", is_income=True
    )
    expense = await create_test_category(db_session, owner_id=user.id, name="Bills")
    await create_test_subcategory(
        db_session, owner_id=user.id, category_id=income.id, name="Salary"
    )
    await create_test_subcategory(
        db_session, owner_id=user.id, category_id=expense.id, name="Rent"
    )
    return user


async def _count(db: AsyncSession, model, owner_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(model).where(model.owner_id == owner_id)
    )
    return result.scalar_one()


async def _generate(client: AsyncClient, user_id: int) -> None:
    response = await client.post(
        f"{PREFIX}/generate-demo-data",
        json={"user_id": user_id, "num_transactions": 1},
    )
    assert response.status_code == 201, response.text


async def test_generate_twice_does_not_duplicate_recurring(
    superuser_client: AsyncClient, db_session: AsyncSession, target_user: User
):
    await _generate(superuser_client, target_user.id)
    after_once = await _count(db_session, RecurringTransaction, target_user.id)
    assert after_once > 0

    await _generate(superuser_client, target_user.id)
    assert await _count(db_session, RecurringTransaction, target_user.id) == after_once


async def test_delete_clears_recurring_detection_state(
    superuser_client: AsyncClient, db_session: AsyncSession, target_user: User
):
    db_session.add_all(
        [
            RecurringDetectionUsage(owner_id=target_user.id, scan_count=1),
            RecurringMerchantAlias(
                owner_id=target_user.id,
                normalized_description="netflix",
                merchant_key="netflix",
                merchant_name="Netflix",
                source="rule",
            ),
            RecurringSuggestionDismissal(
                owner_id=target_user.id, fingerprint="a" * 32, label="Netflix"
            ),
        ]
    )
    await db_session.commit()

    response = await superuser_client.post(
        f"{PREFIX}/delete-user-financial-data", json={"user_id": target_user.id}
    )
    assert response.status_code == 200, response.text

    for model in (
        RecurringDetectionUsage,
        RecurringMerchantAlias,
        RecurringSuggestionDismissal,
    ):
        assert await _count(db_session, model, target_user.id) == 0, model.__name__
