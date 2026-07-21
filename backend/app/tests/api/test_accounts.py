"""
API endpoint tests for /api/v1/accounts.

Covers:
  - GET    /accounts          (list)
  - POST   /accounts          (create)
  - GET    /accounts/{id}     (get by id, 404, permission denied)
  - PUT    /accounts/{id}     (update — uses Body params, not schema)
  - DELETE /accounts/{id}     (delete, clears default_account_id)
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import create_test_account, create_test_user

PREFIX = "/api/v1/accounts"


class TestListAccounts:
    """GET /accounts"""

    async def test_list_accounts(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        await create_test_account(db_session, owner_id=test_user.id, name="Acc A")
        await create_test_account(db_session, owner_id=test_user.id, name="Acc B")

        resp = await client.get(PREFIX)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

    async def test_list_accounts_empty(
        self, client: AsyncClient
    ):
        resp = await client.get(PREFIX)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_list_accounts_only_own(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        """Regular users should only see their own accounts."""
        other_user = await create_test_user(db_session, email="other_acc@test.com")
        await create_test_account(db_session, owner_id=other_user.id, name="Other's Account")
        await create_test_account(db_session, owner_id=test_user.id, name="My Account")

        resp = await client.get(PREFIX)
        assert resp.status_code == 200
        data = resp.json()
        # All returned accounts should belong to test_user
        for acc in data:
            assert acc["owner_id"] == test_user.id


class TestCreateAccount:
    """POST /accounts"""

    async def test_create_account(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={
            "name": "Checking",
            "initial_balance": 1000.0,
            "color": "#FF5733",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Checking"
        assert data["initial_balance"] == 1000.0
        assert data["current_balance"] == 1000.0
        assert data["color"] == "#FF5733"

    async def test_create_account_default_type(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={"name": "Default Type"})
        assert resp.status_code == 200
        # Default type is MISCELLANEOUS (enum value is "Miscellaneous")
        assert resp.json()["type"] == "Miscellaneous"

    async def test_create_account_zero_balance(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={
            "name": "Empty",
            "initial_balance": 0.0,
        })
        assert resp.status_code == 200
        assert resp.json()["current_balance"] == 0.0

    async def test_create_account_invalid_color(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={
            "name": "Bad Color",
            "color": "not-a-color",
        })
        assert resp.status_code == 422


class TestGetAccountById:
    """GET /accounts/{id}"""

    async def test_get_account_by_id(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(
            db_session, owner_id=test_user.id, name="My Savings"
        )
        resp = await client.get(f"{PREFIX}/{account.id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "My Savings"

    async def test_get_account_not_found(self, client: AsyncClient):
        resp = await client.get(f"{PREFIX}/999999")
        assert resp.status_code == 404

    async def test_get_account_other_users_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other_user = await create_test_user(db_session, email="other_acc2@test.com")
        account = await create_test_account(
            db_session, owner_id=other_user.id, name="Private"
        )
        resp = await client.get(f"{PREFIX}/{account.id}")
        assert resp.status_code == 400
        assert "permissions" in resp.json()["detail"].lower()


class TestUpdateAccount:
    """PUT /accounts/{id} — uses Body() params, not a schema."""

    async def test_update_account_name(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(
            db_session, owner_id=test_user.id, name="Old Name"
        )
        resp = await client.put(f"{PREFIX}/{account.id}", json={
            "name": "New Name",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    async def test_update_account_color(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        resp = await client.put(f"{PREFIX}/{account.id}", json={
            "color": "#00FF00",
        })
        assert resp.status_code == 200
        assert resp.json()["color"] == "#00FF00"

    async def test_update_account_initial_balance_adjusts_current(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        """Changing initial_balance should adjust current_balance by the difference."""
        account = await create_test_account(
            db_session, owner_id=test_user.id, initial_balance=1000.0
        )
        resp = await client.put(f"{PREFIX}/{account.id}", json={
            "initial_balance": 1500.0,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["initial_balance"] == 1500.0
        # current_balance should have increased by 500
        assert data["current_balance"] == 1500.0

    async def test_update_nonexistent_account(self, client: AsyncClient):
        resp = await client.put(f"{PREFIX}/999999", json={"name": "x"})
        assert resp.status_code == 404


class TestDeleteAccount:
    """DELETE /accounts/{id}"""

    async def test_delete_account(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        resp = await client.delete(f"{PREFIX}/{account.id}")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

        # Verify it's gone
        resp2 = await client.get(f"{PREFIX}/{account.id}")
        assert resp2.status_code == 404

    async def test_delete_nonexistent_account(self, client: AsyncClient):
        resp = await client.delete(f"{PREFIX}/999999")
        assert resp.status_code == 404

    async def test_delete_account_clears_default(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        """Deleting an account that is the user's default_account_id should clear it."""
        from app import crud

        account = await create_test_account(db_session, owner_id=test_user.id)
        # Set as default
        await crud.user.set_default_account(
            db_session, user_id=test_user.id, account_id=account.id
        )
        updated_user = await crud.user.get(db_session, id=test_user.id)
        assert updated_user.default_account_id == account.id

        # Delete the account
        resp = await client.delete(f"{PREFIX}/{account.id}")
        assert resp.status_code == 200

        # User's default_account_id should now be None
        refreshed_user = await crud.user.get(db_session, id=test_user.id)
        assert refreshed_user.default_account_id is None

    async def test_delete_other_users_account_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other_user = await create_test_user(db_session, email="del_acc@test.com")
        account = await create_test_account(db_session, owner_id=other_user.id)
        resp = await client.delete(f"{PREFIX}/{account.id}")
        assert resp.status_code == 400
