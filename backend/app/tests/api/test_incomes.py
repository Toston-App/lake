"""
API endpoint tests for /api/v1/incomes.

Covers:
  - POST   /incomes          (create)
  - POST   /incomes/bulk     (bulk create)
  - GET    /incomes/getAll   (list)
  - GET    /incomes/{id}     (get by id, 404, permission denied)
  - PUT    /incomes/{id}     (update)
  - DELETE /incomes/{id}     (delete single)
  - DELETE /incomes/bulk/{ids} (bulk delete)

Note: Incomes don't have category_id directly. They have subcategory_id,
and the subcategory references a category.
"""
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_income,
    create_test_subcategory,
    create_test_user,
)

PREFIX = "/api/v1/incomes"


class TestCreateIncome:
    """POST /incomes"""

    async def test_create_income(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        resp = await client.post(PREFIX, json={
            "amount": 1500.0,
            "date": "2025-07-01",
            "description": "Salary",
            "account_id": account.id,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount"] == 1500.0
        assert data["description"] == "Salary"
        assert data["owner_id"] == test_user.id

    async def test_create_income_minimal(self, client: AsyncClient):
        """Only amount is required."""
        resp = await client.post(PREFIX, json={"amount": 100.0})
        assert resp.status_code == 200
        assert resp.json()["amount"] == 100.0

    async def test_create_income_rounds_amount(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={"amount": 99.999})
        assert resp.status_code == 200
        assert resp.json()["amount"] == 100.0

    async def test_create_income_negative_rejected(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={"amount": -50.0})
        assert resp.status_code == 422

    async def test_create_income_invalid_made_from(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={"amount": 10.0, "made_from": "Telegram"})
        assert resp.status_code == 422

    async def test_create_income_with_subcategory(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        category = await create_test_category(
            db_session, owner_id=test_user.id, name="Income Cat", is_income=True
        )
        subcategory = await create_test_subcategory(
            db_session, owner_id=test_user.id, category_id=category.id, name="Freelance"
        )
        resp = await client.post(PREFIX, json={
            "amount": 500.0,
            "subcategory_id": subcategory.id,
        })
        assert resp.status_code == 200
        assert resp.json()["subcategory_id"] == subcategory.id


class TestCreateIncomeBulk:
    """POST /incomes/bulk"""

    async def test_bulk_create(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        incomes = [
            {"amount": 100.0, "date": "2025-07-01", "account_id": account.id},
            {"amount": 200.0, "date": "2025-07-02", "account_id": account.id},
        ]
        resp = await client.post(f"{PREFIX}/bulk", json=incomes)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


class TestGetIncomes:
    """GET /incomes/getAll and GET /incomes/{id}"""

    async def test_get_all_incomes(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        for _ in range(3):
            await create_test_income(db_session, owner_id=test_user.id, amount=500.0)
        resp = await client.get(f"{PREFIX}/getAll")
        assert resp.status_code == 200
        assert len(resp.json()) >= 3

    async def test_get_all_with_pagination(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        for _ in range(5):
            await create_test_income(db_session, owner_id=test_user.id, amount=10.0)
        resp = await client.get(f"{PREFIX}/getAll", params={"skip": 0, "limit": 2})
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_get_income_by_id(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        income = await create_test_income(
            db_session, owner_id=test_user.id, amount=999.0
        )
        resp = await client.get(f"{PREFIX}/{income.id}")
        assert resp.status_code == 200
        assert resp.json()["amount"] == 999.0

    async def test_get_income_not_found(self, client: AsyncClient):
        resp = await client.get(f"{PREFIX}/999999")
        assert resp.status_code == 404

    async def test_get_income_other_users_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other_user = await create_test_user(db_session, email="other_inc@test.com")
        income = await create_test_income(
            db_session, owner_id=other_user.id, amount=500.0
        )
        resp = await client.get(f"{PREFIX}/{income.id}")
        assert resp.status_code == 400
        assert "permissions" in resp.json()["detail"].lower()


class TestUpdateIncome:
    """PUT /incomes/{id}"""

    async def test_update_income_amount(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        income = await create_test_income(
            db_session, owner_id=test_user.id, amount=200.0, account_id=account.id
        )
        resp = await client.put(f"{PREFIX}/{income.id}", json={
            "amount": 350.0,
        })
        assert resp.status_code == 200
        assert resp.json()["amount"] == 350.0

    async def test_update_income_description(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        income = await create_test_income(
            db_session, owner_id=test_user.id, description="Old"
        )
        resp = await client.put(f"{PREFIX}/{income.id}", json={
            "description": "Updated income",
        })
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated income"

    async def test_update_nonexistent_income(self, client: AsyncClient):
        resp = await client.put(f"{PREFIX}/999999", json={"amount": 10.0})
        assert resp.status_code == 404


class TestDeleteIncome:
    """DELETE /incomes/{id}"""

    async def test_delete_income(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        income = await create_test_income(
            db_session, owner_id=test_user.id, amount=100.0
        )
        resp = await client.delete(f"{PREFIX}/{income.id}")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

        # Confirm deleted
        resp2 = await client.get(f"{PREFIX}/{income.id}")
        assert resp2.status_code == 404

    async def test_delete_nonexistent_income(self, client: AsyncClient):
        resp = await client.delete(f"{PREFIX}/999999")
        assert resp.status_code == 404


class TestBulkDeleteIncomes:
    """DELETE /incomes/bulk/{ids}"""

    async def test_bulk_delete(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        i1 = await create_test_income(db_session, owner_id=test_user.id, amount=10.0)
        i2 = await create_test_income(db_session, owner_id=test_user.id, amount=20.0)

        resp = await client.delete(f"{PREFIX}/bulk/{i1.id},{i2.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["deleted_ids"]) == 2

    async def test_bulk_delete_invalid_format(self, client: AsyncClient):
        resp = await client.delete(f"{PREFIX}/bulk/abc,def")
        assert resp.status_code == 400

    async def test_bulk_delete_no_valid_ids(self, client: AsyncClient):
        resp = await client.delete(f"{PREFIX}/bulk/999998,999999")
        assert resp.status_code == 404

    async def test_bulk_delete_other_users_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other_user = await create_test_user(db_session, email="bulk_inc_other@test.com")
        income = await create_test_income(
            db_session, owner_id=other_user.id, amount=50.0
        )
        resp = await client.delete(f"{PREFIX}/bulk/{income.id}")
        assert resp.status_code == 400
