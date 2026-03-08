"""
API endpoint tests for /api/v1/categories.

Covers:
  - GET    /categories          (list, with subcategories loaded)
  - POST   /categories          (create)
  - GET    /categories/{id}     (get by id, uses selectinload for subcategories)
  - PUT    /categories/{id}     (update)
  - DELETE /categories/{id}     (delete, blocked if is_income=True)
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_category,
    create_test_subcategory,
    create_test_user,
)

PREFIX = "/api/v1/categories"


class TestListCategories:
    """GET /categories"""

    async def test_list_categories(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        await create_test_category(db_session, owner_id=test_user.id, name="Food")
        await create_test_category(db_session, owner_id=test_user.id, name="Transport")

        resp = await client.get(PREFIX)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

    async def test_list_categories_includes_subcategories(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        category = await create_test_category(
            db_session, owner_id=test_user.id, name="Entertainment"
        )
        await create_test_subcategory(
            db_session, owner_id=test_user.id, category_id=category.id, name="Movies"
        )
        await create_test_subcategory(
            db_session, owner_id=test_user.id, category_id=category.id, name="Games"
        )

        resp = await client.get(PREFIX)
        assert resp.status_code == 200
        data = resp.json()
        # Find our category in the list
        cat = next(c for c in data if c["id"] == category.id)
        assert len(cat["subcategories"]) == 2

    async def test_list_only_own_categories(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        other_user = await create_test_user(db_session, email="other_cat@test.com")
        await create_test_category(
            db_session, owner_id=other_user.id, name="Other's Category"
        )
        await create_test_category(
            db_session, owner_id=test_user.id, name="My Category"
        )

        resp = await client.get(PREFIX)
        assert resp.status_code == 200
        for cat in resp.json():
            assert cat["owner_id"] == test_user.id


class TestCreateCategory:
    """POST /categories"""

    async def test_create_category(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={
            "name": "Healthcare",
            "color": "#00AAFF",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Healthcare"
        assert data["color"] == "#00AAFF"
        assert data["total"] == 0.0
        assert data["is_income"] is False

    async def test_create_income_category(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={
            "name": "Salary",
            "color": "#33FF00",
            "is_income": True,
        })
        assert resp.status_code == 200
        assert resp.json()["is_income"] is True

    async def test_create_category_invalid_color(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={
            "name": "Bad",
            "color": "notahex",
        })
        assert resp.status_code == 422

    async def test_create_category_empty_name_rejected(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={
            "name": "",
            "color": "#FF0000",
        })
        assert resp.status_code == 422

    async def test_create_category_empty_color_rejected(self, client: AsyncClient):
        resp = await client.post(PREFIX, json={
            "name": "Valid",
            "color": "",
        })
        assert resp.status_code == 422


class TestGetCategoryById:
    """GET /categories/{id}"""

    async def test_get_category_by_id(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        category = await create_test_category(
            db_session, owner_id=test_user.id, name="Shopping"
        )
        resp = await client.get(f"{PREFIX}/{category.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Shopping"
        # Should include subcategories (even if empty list)
        assert "subcategories" in data

    async def test_get_category_with_subcategories(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        category = await create_test_category(
            db_session, owner_id=test_user.id, name="Home"
        )
        sub1 = await create_test_subcategory(
            db_session, owner_id=test_user.id, category_id=category.id, name="Rent"
        )
        sub2 = await create_test_subcategory(
            db_session, owner_id=test_user.id, category_id=category.id, name="Utilities"
        )

        resp = await client.get(f"{PREFIX}/{category.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["subcategories"]) == 2
        sub_names = {s["name"] for s in data["subcategories"]}
        assert sub_names == {"Rent", "Utilities"}

    async def test_get_category_not_found(self, client: AsyncClient):
        resp = await client.get(f"{PREFIX}/999999")
        assert resp.status_code == 404

    async def test_get_category_other_users_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other_user = await create_test_user(db_session, email="other_cat2@test.com")
        category = await create_test_category(
            db_session, owner_id=other_user.id, name="Private"
        )
        resp = await client.get(f"{PREFIX}/{category.id}")
        assert resp.status_code == 403
        assert "permissions" in resp.json()["detail"].lower()


class TestUpdateCategory:
    """PUT /categories/{id}"""

    async def test_update_category_name(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        category = await create_test_category(
            db_session, owner_id=test_user.id, name="Old Name", color="#AABBCC"
        )
        resp = await client.put(f"{PREFIX}/{category.id}", json={
            "name": "New Name",
            "color": "#AABBCC",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    async def test_update_category_color(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        category = await create_test_category(
            db_session, owner_id=test_user.id, name="Cat", color="#111111"
        )
        resp = await client.put(f"{PREFIX}/{category.id}", json={
            "name": "Cat",
            "color": "#FF0000",
        })
        assert resp.status_code == 200
        assert resp.json()["color"] == "#FF0000"

    async def test_update_nonexistent_category(self, client: AsyncClient):
        resp = await client.put(f"{PREFIX}/999999", json={
            "name": "X",
            "color": "#000000",
        })
        assert resp.status_code == 404


class TestDeleteCategory:
    """DELETE /categories/{id}"""

    async def test_delete_category(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        category = await create_test_category(
            db_session, owner_id=test_user.id, name="Deletable"
        )
        resp = await client.delete(f"{PREFIX}/{category.id}")
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

        # Verify it's gone
        resp2 = await client.get(f"{PREFIX}/{category.id}")
        assert resp2.status_code == 404

    async def test_delete_income_category_blocked(
        self, client: AsyncClient, db_session: AsyncSession, test_user
    ):
        """Categories with is_income=True cannot be deleted."""
        category = await create_test_category(
            db_session, owner_id=test_user.id, name="Ingresos", is_income=True
        )
        resp = await client.delete(f"{PREFIX}/{category.id}")
        assert resp.status_code == 400
        assert "cannot be deleted" in resp.json()["detail"].lower()

    async def test_delete_nonexistent_category(self, client: AsyncClient):
        resp = await client.delete(f"{PREFIX}/999999")
        assert resp.status_code == 404

    async def test_delete_other_users_category_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other_user = await create_test_user(db_session, email="del_cat@test.com")
        category = await create_test_category(
            db_session, owner_id=other_user.id, name="Not Mine"
        )
        resp = await client.delete(f"{PREFIX}/{category.id}")
        assert resp.status_code == 403
