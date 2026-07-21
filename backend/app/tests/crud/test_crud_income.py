"""Tests for CRUD income operations."""
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.schemas.income import IncomeCreate
from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_income,
    create_test_place,
    create_test_subcategory,
    create_test_user,
)


class TestCRUDIncomeCreate:
    """Tests for income creation with side effects."""

    async def test_create_with_owner_basic(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_create@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )
        category = await create_test_category(
            db_session, owner_id=user.id, is_income=True
        )
        subcategory = await create_test_subcategory(
            db_session, owner_id=user.id, category_id=category.id
        )

        income_in = IncomeCreate(
            amount=500.0,
            date="2025-02-10",
            description="Salary",
            account_id=account.id,
            subcategory_id=subcategory.id,
        )
        income = await crud.income.create_with_owner(
            db_session, obj_in=income_in, owner_id=user.id
        )

        assert income.id is not None
        assert income.amount == pytest.approx(500.0)
        assert income.date == date(2025, 2, 10)
        assert income.description == "Salary"
        assert income.owner_id == user.id
        assert income.account_id == account.id
        assert income.subcategory_id == subcategory.id

    async def test_create_updates_account_balance(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_acc@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )

        income_in = IncomeCreate(
            amount=300.0,
            date="2025-02-10",
            account_id=account.id,
        )
        await crud.income.create_with_owner(
            db_session, obj_in=income_in, owner_id=user.id
        )

        updated_account = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=account.id
        )
        assert updated_account.current_balance == pytest.approx(1300.0)
        assert updated_account.total_incomes == pytest.approx(300.0)

    async def test_create_updates_user_balance(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session,
            email="inc_user_bal@example.com",
            balance_total=1000.0,
            balance_income=0.0,
        )

        income_in = IncomeCreate(
            amount=400.0,
            date="2025-02-10",
        )
        await crud.income.create_with_owner(
            db_session, obj_in=income_in, owner_id=user.id
        )

        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == pytest.approx(1400.0)
        assert updated_user.balance_income == pytest.approx(400.0)

    async def test_create_updates_subcategory_and_category_total(
        self, db_session: AsyncSession
    ):
        """Income updates both subcategory.total AND its parent category.total."""
        user = await create_test_user(db_session, email="inc_cat@example.com")
        category = await create_test_category(
            db_session, owner_id=user.id, is_income=True, total=0.0
        )
        subcategory = await create_test_subcategory(
            db_session, owner_id=user.id, category_id=category.id, total=0.0
        )

        income_in = IncomeCreate(
            amount=200.0,
            date="2025-02-10",
            subcategory_id=subcategory.id,
        )
        await crud.income.create_with_owner(
            db_session, obj_in=income_in, owner_id=user.id
        )

        updated_sub = await crud.subcategory.get(db_session, id=subcategory.id)
        assert updated_sub.total == pytest.approx(200.0)

        updated_cat = await crud.category.get(db_session, id=category.id)
        assert updated_cat.total == pytest.approx(200.0)

    async def test_create_nullifies_invalid_account(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_null_acc@example.com")

        income_in = IncomeCreate(
            amount=100.0,
            date="2025-02-10",
            account_id=999999,
        )
        income = await crud.income.create_with_owner(
            db_session, obj_in=income_in, owner_id=user.id
        )
        assert income.account_id is None

    async def test_create_nullifies_subcategory_from_other_owner(
        self, db_session: AsyncSession
    ):
        user1 = await create_test_user(db_session, email="inc_owner1@example.com")
        user2 = await create_test_user(db_session, email="inc_owner2@example.com")
        category = await create_test_category(
            db_session, owner_id=user2.id, is_income=True
        )
        subcategory = await create_test_subcategory(
            db_session, owner_id=user2.id, category_id=category.id
        )

        income_in = IncomeCreate(
            amount=100.0,
            date="2025-02-10",
            subcategory_id=subcategory.id,
        )
        income = await crud.income.create_with_owner(
            db_session, obj_in=income_in, owner_id=user1.id
        )
        assert income.subcategory_id is None

    async def test_create_with_invalid_date_sets_none(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_baddate@example.com")

        income_in = IncomeCreate(
            amount=100.0,
            date="invalid-date",
        )
        income = await crud.income.create_with_owner(
            db_session, obj_in=income_in, owner_id=user.id
        )
        assert income.date is None

    async def test_create_with_place(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_place@example.com")
        place = await create_test_place(db_session, owner_id=user.id, name="Office")

        income_in = IncomeCreate(
            amount=500.0,
            date="2025-02-10",
            place_id=place.id,
        )
        income = await crud.income.create_with_owner(
            db_session, obj_in=income_in, owner_id=user.id
        )
        assert income.place_id == place.id


class TestCRUDIncomeGet:
    """Tests for income retrieval operations."""

    async def test_get_by_id(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_get@example.com")
        income = await create_test_income(db_session, owner_id=user.id)
        fetched = await crud.income.get(db_session, id=income.id)
        assert fetched is not None
        assert fetched.id == income.id

    async def test_get_multi_by_owner(self, db_session: AsyncSession):
        user1 = await create_test_user(db_session, email="inc_multi1@example.com")
        user2 = await create_test_user(db_session, email="inc_multi2@example.com")

        await create_test_income(db_session, owner_id=user1.id, description="u1_i1")
        await create_test_income(db_session, owner_id=user1.id, description="u1_i2")
        await create_test_income(db_session, owner_id=user2.id, description="u2_i1")

        incomes_u1 = await crud.income.get_multi_by_owner(
            db_session, owner_id=user1.id
        )
        incomes_u2 = await crud.income.get_multi_by_owner(
            db_session, owner_id=user2.id
        )
        assert len(incomes_u1) == 2
        assert len(incomes_u2) == 1

    async def test_get_multi_by_date(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_date@example.com")
        await create_test_income(
            db_session, owner_id=user.id, income_date=date(2025, 3, 5)
        )
        await create_test_income(
            db_session, owner_id=user.id, income_date=date(2025, 3, 15)
        )
        await create_test_income(
            db_session, owner_id=user.id, income_date=date(2025, 4, 1)
        )

        march = await crud.income.get_multi_by_date(
            db_session,
            owner_id=user.id,
            start_date=date(2025, 3, 1),
            end_date=date(2025, 3, 31),
        )
        assert len(march) == 2

    async def test_get_multi_by_date_ordered(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_order@example.com")
        await create_test_income(
            db_session, owner_id=user.id, income_date=date(2025, 3, 20)
        )
        await create_test_income(
            db_session, owner_id=user.id, income_date=date(2025, 3, 5)
        )

        incomes = await crud.income.get_multi_by_date(
            db_session,
            owner_id=user.id,
            start_date=date(2025, 3, 1),
            end_date=date(2025, 3, 31),
        )
        assert len(incomes) == 2
        assert incomes[0].date <= incomes[1].date


class TestCRUDIncomeRemove:
    """Tests for income removal operations."""

    async def test_remove_single(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_rm@example.com")
        income = await create_test_income(db_session, owner_id=user.id)
        removed = await crud.income.remove(db_session, id=income.id)
        assert removed.id == income.id

        fetched = await crud.income.get(db_session, id=income.id)
        assert fetched is None
