"""Tests for CRUD user operations."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.schemas.user import UserCreate, UserUpdate
from tests.utils import create_test_user


class TestCRUDUserGet:
    """Tests for user retrieval operations."""

    async def test_get_user_by_id(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="getid@example.com")
        fetched = await crud.user.get(db_session, id=user.id)
        assert fetched is not None
        assert fetched.id == user.id
        assert fetched.email == "getid@example.com"

    async def test_get_user_by_id_nonexistent(self, db_session: AsyncSession):
        fetched = await crud.user.get(db_session, id=999999)
        assert fetched is None

    async def test_get_by_email(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="byemail@example.com")
        fetched = await crud.user.get_by_email(db_session, email="byemail@example.com")
        assert fetched is not None
        assert fetched.id == user.id

    async def test_get_by_email_nonexistent(self, db_session: AsyncSession):
        fetched = await crud.user.get_by_email(db_session, email="nonexistent@example.com")
        assert fetched is None

    async def test_get_by_uuid(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="uuid_user@example.com")
        # Set UUID directly since create_test_user doesn't set it
        user.uuid = "test-uuid-123"
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        fetched = await crud.user.get_by_uuid(db_session, uuid="test-uuid-123")
        assert fetched is not None
        assert fetched.id == user.id

    async def test_get_by_uuid_nonexistent(self, db_session: AsyncSession):
        fetched = await crud.user.get_by_uuid(db_session, uuid="nonexistent-uuid")
        assert fetched is None


class TestCRUDUserCreate:
    """Tests for user creation via CRUD (with category seeding)."""

    async def test_create_user(self, db_session: AsyncSession):
        """Creating a user via CRUD triggers category seeding."""
        user_in = UserCreate(
            email="newuser@example.com",
            password="securepassword",
            name="New User",
            country="USD",
        )
        user = await crud.user.create(db_session, obj_in=user_in)
        assert user.id is not None
        assert user.email == "newuser@example.com"
        assert user.name == "New User"
        assert user.country == "USD"
        assert user.is_superuser is False
        assert user.is_active is True
        # Password should be hashed, not stored raw
        assert user.hashed_password != "securepassword"

    async def test_create_superuser(self, db_session: AsyncSession):
        user_in = UserCreate(
            email="superuser@example.com",
            password="adminpassword",
            name="Super User",
            country="MXN",
            is_superuser=True,
        )
        user = await crud.user.create(db_session, obj_in=user_in)
        assert user.is_superuser is True


class TestCRUDUserUpdate:
    """Tests for user update operations."""

    async def test_update_user_name(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="update_name@example.com")
        user_in = UserUpdate(name="Updated Name")
        updated = await crud.user.update(db_session, db_obj=user, obj_in=user_in)
        assert updated.name == "Updated Name"
        assert updated.id == user.id  # ID should not change

    async def test_update_user_with_dict(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="update_dict@example.com")
        updated = await crud.user.update(
            db_session, db_obj=user, obj_in={"name": "Dict Updated"}
        )
        assert updated.name == "Dict Updated"

    async def test_update_user_password(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session, email="update_pass@example.com", password="oldpassword"
        )
        old_hash = user.hashed_password
        user_in = UserUpdate(password="newpassword")
        updated = await crud.user.update(db_session, db_obj=user, obj_in=user_in)
        assert updated.hashed_password != old_hash

    async def test_update_cannot_change_own_id(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="update_id@example.com")
        original_id = user.id
        user_in = UserUpdate(name="Hacker")
        # The CRUDUser.update method always resets id to db_obj.id
        updated = await crud.user.update(db_session, db_obj=user, obj_in=user_in)
        assert updated.id == original_id


class TestCRUDUserAuthenticate:
    """Tests for user authentication."""

    async def test_authenticate_valid(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session, email="auth_valid@example.com", password="correctpassword"
        )
        authenticated = await crud.user.authenticate(
            db_session, email="auth_valid@example.com", password="correctpassword"
        )
        assert authenticated is not None
        assert authenticated.id == user.id

    async def test_authenticate_wrong_password(self, db_session: AsyncSession):
        await create_test_user(
            db_session, email="auth_wrong@example.com", password="correctpassword"
        )
        authenticated = await crud.user.authenticate(
            db_session, email="auth_wrong@example.com", password="wrongpassword"
        )
        assert authenticated is None

    async def test_authenticate_nonexistent_email(self, db_session: AsyncSession):
        authenticated = await crud.user.authenticate(
            db_session, email="nosuchuser@example.com", password="anypassword"
        )
        assert authenticated is None


class TestCRUDUserBalance:
    """Tests for user balance update operations."""

    async def test_update_balance_expense(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session,
            email="balance_exp@example.com",
            balance_total=1000.0,
            balance_outcome=0.0,
        )
        updated = await crud.user.update_balance(
            db_session, user_id=user.id, is_Expense=True, amount=150.0
        )
        assert updated.balance_total == pytest.approx(850.0)
        assert updated.balance_outcome == pytest.approx(150.0)

    async def test_update_balance_income(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session,
            email="balance_inc@example.com",
            balance_total=1000.0,
            balance_income=0.0,
        )
        updated = await crud.user.update_balance(
            db_session, user_id=user.id, is_Expense=False, amount=300.0
        )
        assert updated.balance_total == pytest.approx(1300.0)
        assert updated.balance_income == pytest.approx(300.0)

    async def test_update_balance_multiple_expenses(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session,
            email="balance_multi@example.com",
            balance_total=1000.0,
            balance_outcome=0.0,
        )
        await crud.user.update_balance(
            db_session, user_id=user.id, is_Expense=True, amount=100.0
        )
        updated = await crud.user.update_balance(
            db_session, user_id=user.id, is_Expense=True, amount=200.0
        )
        assert updated.balance_total == pytest.approx(700.0)
        assert updated.balance_outcome == pytest.approx(300.0)


class TestCRUDUserHelpers:
    """Tests for is_active and is_superuser helpers."""

    async def test_is_active_true(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session, email="active@example.com", is_active=True
        )
        assert crud.user.is_active(user) is True

    async def test_is_active_false(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session, email="inactive@example.com", is_active=False
        )
        assert crud.user.is_active(user) is False

    async def test_is_superuser_true(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session, email="super@example.com", is_superuser=True
        )
        assert crud.user.is_superuser(user) is True

    async def test_is_superuser_false(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session, email="regular@example.com", is_superuser=False
        )
        assert crud.user.is_superuser(user) is False
