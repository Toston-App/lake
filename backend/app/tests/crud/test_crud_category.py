"""Tests for CRUD category operations."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.schemas.category import CategoryCreate, CategoryUpdate
from tests.utils import (
    create_test_category,
    create_test_subcategory,
    create_test_user,
)


class TestCRUDCategoryCreate:
    """Tests for category creation."""

    async def test_create_with_owner_basic(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="cat_create@example.com")
        category_in = CategoryCreate(
            name="Food",
            color="#FF5733",
            icon="utensils",
        )
        category = await crud.category.create_with_owner(
            db_session, obj_in=category_in, owner_id=user.id
        )
        assert category.id is not None
        assert category.name == "Food"
        assert category.color == "#FF5733"
        assert category.icon == "utensils"
        assert category.owner_id == user.id
        assert category.is_default is False
        assert category.is_income is False
        assert category.total == pytest.approx(0.0)

    async def test_create_income_category(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="cat_income@example.com")
        category_in = CategoryCreate(
            name="Salary",
            color="#00FF00",
            is_income=True,
        )
        category = await crud.category.create_with_owner(
            db_session, obj_in=category_in, owner_id=user.id
        )
        assert category.is_income is True

    async def test_create_default_category(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="cat_default@example.com")
        category_in = CategoryCreate(
            name="Default Cat",
            color="#AABBCC",
            is_default=True,
        )
        category = await crud.category.create_with_owner(
            db_session, obj_in=category_in, owner_id=user.id
        )
        assert category.is_default is True

    async def test_create_returns_with_subcategories_loaded(
        self, db_session: AsyncSession
    ):
        """create_with_owner re-fetches with selectinload for subcategories."""
        user = await create_test_user(db_session, email="cat_sub_load@example.com")
        category_in = CategoryCreate(name="Transport", color="#112233")
        category = await crud.category.create_with_owner(
            db_session, obj_in=category_in, owner_id=user.id
        )
        # subcategories should be loaded (empty list, not lazy error)
        assert category.subcategories == []


class TestCRUDCategoryGet:
    """Tests for category retrieval operations."""

    async def test_get_multi_by_owner(self, db_session: AsyncSession):
        user1 = await create_test_user(db_session, email="cat_multi1@example.com")
        user2 = await create_test_user(db_session, email="cat_multi2@example.com")

        await create_test_category(db_session, owner_id=user1.id, name="Cat A")
        await create_test_category(db_session, owner_id=user1.id, name="Cat B")
        await create_test_category(db_session, owner_id=user2.id, name="Cat C")

        cats_u1 = await crud.category.get_multi_by_owner(
            db_session, owner_id=user1.id
        )
        cats_u2 = await crud.category.get_multi_by_owner(
            db_session, owner_id=user2.id
        )
        assert len(cats_u1) == 2
        assert len(cats_u2) == 1

    async def test_get_multi_by_owner_ordered_by_name(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="cat_order@example.com")
        await create_test_category(db_session, owner_id=user.id, name="Zebra")
        await create_test_category(db_session, owner_id=user.id, name="Alpha")

        cats = await crud.category.get_multi_by_owner(
            db_session, owner_id=user.id
        )
        assert cats[0].name == "Alpha"
        assert cats[1].name == "Zebra"

    async def test_get_multi_by_owner_with_subcategories(
        self, db_session: AsyncSession
    ):
        """get_multi_by_owner uses selectinload for subcategories."""
        user = await create_test_user(db_session, email="cat_sub@example.com")
        category = await create_test_category(
            db_session, owner_id=user.id, name="With Subs"
        )
        await create_test_subcategory(
            db_session,
            owner_id=user.id,
            category_id=category.id,
            name="Sub 1",
        )
        await create_test_subcategory(
            db_session,
            owner_id=user.id,
            category_id=category.id,
            name="Sub 2",
        )

        cats = await crud.category.get_multi_by_owner(
            db_session, owner_id=user.id
        )
        assert len(cats) == 1
        assert len(cats[0].subcategories) == 2

    async def test_get_multi_all(self, db_session: AsyncSession):
        """get_multi returns all categories across all users."""
        user1 = await create_test_user(db_session, email="cat_all1@example.com")
        user2 = await create_test_user(db_session, email="cat_all2@example.com")

        await create_test_category(db_session, owner_id=user1.id, name="Global A")
        await create_test_category(db_session, owner_id=user2.id, name="Global B")

        all_cats = await crud.category.get_multi(db_session)
        # At minimum, we should have our 2 categories
        assert len(all_cats) >= 2


class TestCRUDCategoryUpdate:
    """Tests for category update operations."""

    async def test_update_category_name(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="cat_upd@example.com")
        category = await create_test_category(
            db_session, owner_id=user.id, name="Old Name"
        )
        updated = await crud.category.update(
            db_session, db_obj=category, obj_in={"name": "New Name"}
        )
        assert updated.name == "New Name"

    async def test_update_category_color(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="cat_upd_color@example.com")
        category = await create_test_category(
            db_session, owner_id=user.id, color="#FF5733"
        )
        cat_update = CategoryUpdate(color="#00FF00")
        updated = await crud.category.update(
            db_session, db_obj=category, obj_in=cat_update
        )
        assert updated.color == "#00FF00"


class TestCRUDCategoryRemove:
    """Tests for category removal."""

    async def test_remove_category(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="cat_rm@example.com")
        category = await create_test_category(db_session, owner_id=user.id)
        removed = await crud.category.remove(db_session, id=category.id)
        assert removed.id == category.id

        fetched = await crud.category.get(db_session, id=category.id)
        assert fetched is None
