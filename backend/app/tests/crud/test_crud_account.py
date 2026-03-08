"""Tests for CRUD account operations."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.models.account import AccountType
from app.schemas.account import AccountCreate, AccountUpdate
from tests.utils import create_test_account, create_test_user


class TestCRUDAccountCreate:
    """Tests for account creation with side effects."""

    async def test_create_with_owner_basic(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_create@example.com")
        account_in = AccountCreate(
            name="My Checking",
            type=AccountType.CHECKING,
            initial_balance=500.0,
            color="#FF0000",
        )
        account = await crud.account.create_with_owner(
            db_session, obj_in=account_in, owner_id=user.id
        )
        assert account.id is not None
        assert account.name == "My Checking"
        assert account.type == AccountType.CHECKING
        assert account.initial_balance == pytest.approx(500.0)
        assert account.current_balance == pytest.approx(500.0)
        assert account.owner_id == user.id
        assert account.color == "#FF0000"

    async def test_create_sets_current_balance_from_initial(
        self, db_session: AsyncSession
    ):
        user = await create_test_user(db_session, email="acc_init@example.com")
        account_in = AccountCreate(name="Savings", initial_balance=1500.0)
        account = await crud.account.create_with_owner(
            db_session, obj_in=account_in, owner_id=user.id
        )
        assert account.current_balance == pytest.approx(1500.0)

    async def test_create_updates_user_balance_total(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session,
            email="acc_user_bal@example.com",
            balance_total=0.0,
        )
        account_in = AccountCreate(name="Cash", initial_balance=2000.0)
        await crud.account.create_with_owner(
            db_session, obj_in=account_in, owner_id=user.id
        )
        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == pytest.approx(2000.0)

    async def test_create_zero_initial_balance_no_user_update(
        self, db_session: AsyncSession
    ):
        user = await create_test_user(
            db_session,
            email="acc_zero@example.com",
            balance_total=500.0,
        )
        account_in = AccountCreate(name="Empty Account", initial_balance=0.0)
        await crud.account.create_with_owner(
            db_session, obj_in=account_in, owner_id=user.id
        )
        updated_user = await crud.user.get(db_session, id=user.id)
        # balance_total should remain unchanged for zero initial balance
        assert updated_user.balance_total == pytest.approx(500.0)

    async def test_create_with_default_type(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_default@example.com")
        account_in = AccountCreate(name="Default Type")
        account = await crud.account.create_with_owner(
            db_session, obj_in=account_in, owner_id=user.id
        )
        assert account.type == AccountType.MISCELLANEOUS


class TestCRUDAccountGet:
    """Tests for account retrieval operations."""

    async def test_get_by_id(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_getid@example.com")
        account = await create_test_account(db_session, owner_id=user.id, name="Test")
        fetched = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=account.id
        )
        assert fetched is not None
        assert fetched.id == account.id
        assert fetched.name == "Test"

    async def test_get_by_id_wrong_owner(self, db_session: AsyncSession):
        user1 = await create_test_user(db_session, email="acc_owner1@example.com")
        user2 = await create_test_user(db_session, email="acc_owner2@example.com")
        account = await create_test_account(db_session, owner_id=user1.id)
        fetched = await crud.account.get_by_id(
            db_session, owner_id=user2.id, id=account.id
        )
        assert fetched is None

    async def test_get_by_id_nonexistent(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_noexist@example.com")
        fetched = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=999999
        )
        assert fetched is None

    async def test_get_multi_by_owner(self, db_session: AsyncSession):
        user1 = await create_test_user(db_session, email="acc_multi1@example.com")
        user2 = await create_test_user(db_session, email="acc_multi2@example.com")

        await create_test_account(db_session, owner_id=user1.id, name="Account A")
        await create_test_account(db_session, owner_id=user1.id, name="Account B")
        await create_test_account(db_session, owner_id=user2.id, name="Account C")

        accounts_u1 = await crud.account.get_multi_by_owner(
            db_session, owner_id=user1.id
        )
        accounts_u2 = await crud.account.get_multi_by_owner(
            db_session, owner_id=user2.id
        )
        assert len(accounts_u1) == 2
        assert len(accounts_u2) == 1
        assert all(a.owner_id == user1.id for a in accounts_u1)

    async def test_get_multi_by_owner_ordered_by_name(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_order@example.com")
        await create_test_account(db_session, owner_id=user.id, name="Zebra")
        await create_test_account(db_session, owner_id=user.id, name="Alpha")

        accounts = await crud.account.get_multi_by_owner(
            db_session, owner_id=user.id
        )
        assert accounts[0].name == "Alpha"
        assert accounts[1].name == "Zebra"


class TestCRUDAccountUpdateByField:
    """Tests for update_by_id_and_field operations."""

    async def test_update_total_expenses(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_upd_exp@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )

        result = await crud.account.update_by_id_and_field(
            db_session,
            owner_id=user.id,
            id=account.id,
            column="total_expenses",
            amount=200.0,
        )
        assert result is not None
        # Re-fetch for accurate values
        updated = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=account.id
        )
        assert updated.current_balance == pytest.approx(800.0)
        assert updated.total_expenses == pytest.approx(200.0)

    async def test_update_total_incomes(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_upd_inc@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )

        await crud.account.update_by_id_and_field(
            db_session,
            owner_id=user.id,
            id=account.id,
            column="total_incomes",
            amount=500.0,
        )
        updated = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=account.id
        )
        assert updated.current_balance == pytest.approx(1500.0)
        assert updated.total_incomes == pytest.approx(500.0)

    async def test_update_total_transfers_in(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_upd_tin@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )

        await crud.account.update_by_id_and_field(
            db_session,
            owner_id=user.id,
            id=account.id,
            column="total_transfers_in",
            amount=300.0,
        )
        updated = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=account.id
        )
        assert updated.current_balance == pytest.approx(1300.0)
        assert updated.total_transfers_in == pytest.approx(300.0)

    async def test_update_total_transfers_out(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="acc_upd_tout@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )

        await crud.account.update_by_id_and_field(
            db_session,
            owner_id=user.id,
            id=account.id,
            column="total_transfers_out",
            amount=400.0,
        )
        updated = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=account.id
        )
        assert updated.current_balance == pytest.approx(600.0)
        assert updated.total_transfers_out == pytest.approx(400.0)

    async def test_update_nonexistent_account_returns_none(
        self, db_session: AsyncSession
    ):
        user = await create_test_user(db_session, email="acc_upd_none@example.com")
        result = await crud.account.update_by_id_and_field(
            db_session,
            owner_id=user.id,
            id=999999,
            column="total_expenses",
            amount=100.0,
        )
        assert result is None

    async def test_update_wrong_owner_returns_none(self, db_session: AsyncSession):
        user1 = await create_test_user(db_session, email="acc_upd_own1@example.com")
        user2 = await create_test_user(db_session, email="acc_upd_own2@example.com")
        account = await create_test_account(
            db_session, owner_id=user1.id, initial_balance=1000.0
        )

        result = await crud.account.update_by_id_and_field(
            db_session,
            owner_id=user2.id,
            id=account.id,
            column="total_expenses",
            amount=100.0,
        )
        assert result is None
