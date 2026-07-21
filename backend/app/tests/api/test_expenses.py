"""
API endpoint tests for /api/v1/expenses.

Covers:
  - POST   /expenses          (create)
  - POST   /expenses/bulk     (bulk create)
  - GET    /expenses/getAll   (list)
  - GET    /expenses/{filter}/{date}  (date filters — covered in business_logic/)
  - GET    /expenses/{id}     (get by id, 404, permission denied)
  - PUT    /expenses/{id}     (update)
  - DELETE /expenses/{id}     (delete single)
  - DELETE /expenses/bulk/{ids} (bulk delete)
"""
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_expense,
    create_test_subcategory,
    create_test_user,
)

PREFIX = "/api/v1/expenses"


class TestCreateExpense:
    """POST /expenses"""

    async def test_create_expense(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        resp = await client.post(PREFIX, json={
            "amount": 42.50,
            "date": "2025-06-15",
            "description": "Lunch",
            "account_id": account.id,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount"] == 42.50
        assert data["description"] == "Lunch"
        assert data["owner_id"] == test_user.id
        assert data["account_id"] == account.id

    async def test_create_expense_minimal(self, client: AsyncClient):
        """Only amount is required."""
        resp = await client.post(PREFIX, json={"amount": 10.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount"] == 10.0
        assert data["account_id"] is None

    async def test_create_expense_rounds_amount(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={"amount": 9.999})
        assert resp.status_code == 200
        assert resp.json()["amount"] == 10.0

    async def test_create_expense_negative_amount_rejected(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={"amount": -5.0})
        assert resp.status_code == 422

    async def test_create_expense_invalid_made_from(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={"amount": 10.0, "made_from": "Invalid"})
        assert resp.status_code == 422

    async def test_create_expense_with_category(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        category = await create_test_category(db_session, owner_id=test_user.id)
        subcategory = await create_test_subcategory(
            db_session, owner_id=test_user.id, category_id=category.id
        )
        resp = await client.post(PREFIX, json={
            "amount": 20.0,
            "category_id": category.id,
            "subcategory_id": subcategory.id,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["category_id"] == category.id
        assert data["subcategory_id"] == subcategory.id


class TestCreateExpenseBulk:
    """POST /expenses/bulk"""

    async def test_bulk_create(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        expenses = [
            {"amount": 10.0, "date": "2025-06-01", "account_id": account.id},
            {"amount": 20.0, "date": "2025-06-02", "account_id": account.id},
            {"amount": 30.0, "date": "2025-06-03", "account_id": account.id},
        ]
        resp = await client.post(f"{PREFIX}/bulk", json=expenses)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        amounts = [e["amount"] for e in data]
        assert amounts == [10.0, 20.0, 30.0]


class TestGetExpenses:
    """GET /expenses/getAll and GET /expenses/{id}"""

    async def test_get_all_expenses(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        for i in range(3):
            await create_test_expense(
                db_session, owner_id=test_user.id, amount=float(10 * (i + 1))
            )
        resp = await client.get(f"{PREFIX}/getAll")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3

    async def test_get_all_with_pagination(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        for i in range(5):
            await create_test_expense(db_session, owner_id=test_user.id, amount=1.0)
        resp = await client.get(f"{PREFIX}/getAll", params={"skip": 0, "limit": 2})
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_get_expense_by_id(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        expense = await create_test_expense(
            db_session, owner_id=test_user.id, amount=77.0
        )
        resp = await client.get(f"{PREFIX}/{expense.id}")
        assert resp.status_code == 200
        assert resp.json()["amount"] == 77.0
        assert resp.json()["id"] == expense.id

    async def test_get_expense_not_found(self, client: AsyncClient):
        resp = await client.get(f"{PREFIX}/999999")
        assert resp.status_code == 404

    async def test_get_expense_other_users_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other_user = await create_test_user(
            db_session, email="other_exp@test.com"
        )
        expense = await create_test_expense(
            db_session, owner_id=other_user.id, amount=50.0
        )
        resp = await client.get(f"{PREFIX}/{expense.id}")
        assert resp.status_code == 400
        assert "permissions" in resp.json()["detail"].lower()


class TestUpdateExpense:
    """PUT /expenses/{id}"""

    async def test_update_expense_amount(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        expense = await create_test_expense(
            db_session, owner_id=test_user.id, amount=100.0, account_id=account.id
        )
        resp = await client.put(f"{PREFIX}/{expense.id}", json={
            "amount": 150.0,
        })
        assert resp.status_code == 200
        assert resp.json()["amount"] == 150.0

    async def test_update_expense_description(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        expense = await create_test_expense(
            db_session, owner_id=test_user.id, description="Old"
        )
        resp = await client.put(f"{PREFIX}/{expense.id}", json={
            "description": "New description",
        })
        assert resp.status_code == 200
        assert resp.json()["description"] == "New description"

    async def test_update_nonexistent_expense(self, client: AsyncClient):
        resp = await client.put(f"{PREFIX}/999999", json={"amount": 10.0})
        assert resp.status_code == 404


class TestDeleteExpense:
    """DELETE /expenses/{id}"""

    async def test_delete_expense(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        expense = await create_test_expense(
            db_session, owner_id=test_user.id, amount=25.0
        )
        resp = await client.delete(f"{PREFIX}/{expense.id}")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

        # Confirm it's gone
        resp2 = await client.get(f"{PREFIX}/{expense.id}")
        assert resp2.status_code == 404

    async def test_delete_nonexistent_expense(self, client: AsyncClient):
        resp = await client.delete(f"{PREFIX}/999999")
        assert resp.status_code == 404


class TestBulkDeleteExpenses:
    """DELETE /expenses/bulk/{ids}"""

    async def test_bulk_delete(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        e1 = await create_test_expense(db_session, owner_id=test_user.id, amount=10.0)
        e2 = await create_test_expense(db_session, owner_id=test_user.id, amount=20.0)
        e3 = await create_test_expense(db_session, owner_id=test_user.id, amount=30.0)

        resp = await client.delete(f"{PREFIX}/bulk/{e1.id},{e2.id},{e3.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["deleted_ids"]) == 3

    async def test_bulk_delete_invalid_format(self, client: AsyncClient):
        resp = await client.delete(f"{PREFIX}/bulk/abc,def")
        assert resp.status_code == 400

    async def test_bulk_delete_no_valid_ids(self, client: AsyncClient):
        resp = await client.delete(f"{PREFIX}/bulk/999998,999999")
        assert resp.status_code == 404

    async def test_bulk_delete_other_users_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other_user = await create_test_user(db_session, email="bulk_other@test.com")
        expense = await create_test_expense(
            db_session, owner_id=other_user.id, amount=50.0
        )
        resp = await client.delete(f"{PREFIX}/bulk/{expense.id}")
        assert resp.status_code == 400
