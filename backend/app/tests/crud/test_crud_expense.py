"""Tests for CRUD expense operations."""
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.schemas.expense import ExpenseCreate
from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_expense,
    create_test_place,
    create_test_subcategory,
    create_test_user,
)


class TestCRUDExpenseCreate:
    """Tests for expense creation with side effects."""

    async def test_create_with_owner_basic(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_create@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )
        category = await create_test_category(db_session, owner_id=user.id)
        subcategory = await create_test_subcategory(
            db_session, owner_id=user.id, category_id=category.id
        )

        expense_in = ExpenseCreate(
            amount=150.50,
            date="2025-01-15",
            description="Test expense",
            account_id=account.id,
            category_id=category.id,
            subcategory_id=subcategory.id,
        )
        expense = await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )

        assert expense.id is not None
        assert expense.amount == pytest.approx(150.50)
        assert expense.date == date(2025, 1, 15)
        assert expense.description == "Test expense"
        assert expense.owner_id == user.id
        assert expense.account_id == account.id
        assert expense.category_id == category.id
        assert expense.subcategory_id == subcategory.id

    async def test_create_updates_account_balance(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_acc@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=1000.0
        )
        category = await create_test_category(db_session, owner_id=user.id)

        expense_in = ExpenseCreate(
            amount=250.0,
            date="2025-01-15",
            account_id=account.id,
            category_id=category.id,
        )
        await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )

        # Re-fetch account to check balance update
        updated_account = await crud.account.get_by_id(
            db_session, owner_id=user.id, id=account.id
        )
        assert updated_account.current_balance == pytest.approx(750.0)
        assert updated_account.total_expenses == pytest.approx(250.0)

    async def test_create_updates_user_balance(self, db_session: AsyncSession):
        user = await create_test_user(
            db_session,
            email="exp_user_bal@example.com",
            balance_total=1000.0,
            balance_outcome=0.0,
        )
        account = await create_test_account(db_session, owner_id=user.id)
        category = await create_test_category(db_session, owner_id=user.id)

        expense_in = ExpenseCreate(
            amount=200.0,
            date="2025-01-15",
            account_id=account.id,
            category_id=category.id,
        )
        await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )

        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == pytest.approx(800.0)
        assert updated_user.balance_outcome == pytest.approx(200.0)

    async def test_create_updates_category_total(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_cat@example.com")
        account = await create_test_account(db_session, owner_id=user.id)
        category = await create_test_category(
            db_session, owner_id=user.id, total=0.0
        )

        expense_in = ExpenseCreate(
            amount=100.0,
            date="2025-01-15",
            account_id=account.id,
            category_id=category.id,
        )
        await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )

        updated_category = await crud.category.get(db_session, id=category.id)
        assert updated_category.total == pytest.approx(100.0)

    async def test_create_updates_subcategory_total(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_sub@example.com")
        account = await create_test_account(db_session, owner_id=user.id)
        category = await create_test_category(db_session, owner_id=user.id)
        subcategory = await create_test_subcategory(
            db_session, owner_id=user.id, category_id=category.id, total=0.0
        )

        expense_in = ExpenseCreate(
            amount=75.0,
            date="2025-01-15",
            account_id=account.id,
            category_id=category.id,
            subcategory_id=subcategory.id,
        )
        await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )

        updated_sub = await crud.subcategory.get(db_session, id=subcategory.id)
        assert updated_sub.total == pytest.approx(75.0)

    async def test_create_nullifies_invalid_account(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_null_acc@example.com")
        category = await create_test_category(db_session, owner_id=user.id)

        expense_in = ExpenseCreate(
            amount=50.0,
            date="2025-01-15",
            account_id=999999,  # nonexistent
            category_id=category.id,
        )
        expense = await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )
        assert expense.account_id is None

    async def test_create_nullifies_category_from_other_owner(
        self, db_session: AsyncSession
    ):
        user1 = await create_test_user(db_session, email="exp_owner1@example.com")
        user2 = await create_test_user(db_session, email="exp_owner2@example.com")
        category = await create_test_category(db_session, owner_id=user2.id)

        expense_in = ExpenseCreate(
            amount=50.0,
            date="2025-01-15",
            category_id=category.id,
        )
        expense = await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user1.id
        )
        assert expense.category_id is None

    async def test_create_with_invalid_date_sets_none(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_baddate@example.com")

        expense_in = ExpenseCreate(
            amount=50.0,
            date="not-a-date",
        )
        expense = await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )
        assert expense.date is None

    async def test_create_with_place(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_place@example.com")
        place = await create_test_place(db_session, owner_id=user.id, name="Cafe")

        expense_in = ExpenseCreate(
            amount=25.0,
            date="2025-01-15",
            place_id=place.id,
        )
        expense = await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )
        assert expense.place_id == place.id

    async def test_create_rounds_amount(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_round@example.com")

        expense_in = ExpenseCreate(
            amount=99.999,
            date="2025-01-15",
        )
        expense = await crud.expense.create_with_owner(
            db_session, obj_in=expense_in, owner_id=user.id
        )
        assert expense.amount == pytest.approx(100.0)


class TestCRUDExpenseBulkCreate:
    """Tests for bulk expense creation."""

    async def test_create_multi_with_owner(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_bulk@example.com")
        account = await create_test_account(
            db_session, owner_id=user.id, initial_balance=5000.0
        )
        category = await create_test_category(db_session, owner_id=user.id)

        expenses_in = [
            ExpenseCreate(
                amount=100.0,
                date="2025-01-15",
                description=f"Bulk expense {i}",
                account_id=account.id,
                category_id=category.id,
            )
            for i in range(3)
        ]
        created = await crud.expense.create_multi_with_owner(
            db_session, obj_list=expenses_in, owner_id=user.id
        )
        assert len(created) == 3
        for exp in created:
            assert exp.owner_id == user.id


class TestCRUDExpenseGet:
    """Tests for expense retrieval operations."""

    async def test_get_by_id(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_get@example.com")
        expense = await create_test_expense(db_session, owner_id=user.id)
        fetched = await crud.expense.get(db_session, id=expense.id)
        assert fetched is not None
        assert fetched.id == expense.id

    async def test_get_multi_by_owner(self, db_session: AsyncSession):
        user1 = await create_test_user(db_session, email="exp_multi1@example.com")
        user2 = await create_test_user(db_session, email="exp_multi2@example.com")

        await create_test_expense(db_session, owner_id=user1.id, description="u1_e1")
        await create_test_expense(db_session, owner_id=user1.id, description="u1_e2")
        await create_test_expense(db_session, owner_id=user2.id, description="u2_e1")

        expenses_u1 = await crud.expense.get_multi_by_owner(
            db_session, owner_id=user1.id
        )
        expenses_u2 = await crud.expense.get_multi_by_owner(
            db_session, owner_id=user2.id
        )
        assert len(expenses_u1) == 2
        assert len(expenses_u2) == 1
        assert all(e.owner_id == user1.id for e in expenses_u1)

    async def test_get_multi_by_owner_with_pagination(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_page@example.com")
        for i in range(5):
            await create_test_expense(
                db_session, owner_id=user.id, description=f"page_{i}"
            )

        page1 = await crud.expense.get_multi_by_owner(
            db_session, owner_id=user.id, skip=0, limit=2
        )
        page2 = await crud.expense.get_multi_by_owner(
            db_session, owner_id=user.id, skip=2, limit=2
        )
        assert len(page1) == 2
        assert len(page2) == 2

    async def test_get_multi_by_date(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_date@example.com")
        await create_test_expense(
            db_session, owner_id=user.id, expense_date=date(2025, 1, 10)
        )
        await create_test_expense(
            db_session, owner_id=user.id, expense_date=date(2025, 1, 20)
        )
        await create_test_expense(
            db_session, owner_id=user.id, expense_date=date(2025, 2, 5)
        )

        january = await crud.expense.get_multi_by_date(
            db_session,
            owner_id=user.id,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
        )
        assert len(january) == 2

    async def test_get_multi_by_date_ordered(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_order@example.com")
        await create_test_expense(
            db_session, owner_id=user.id, expense_date=date(2025, 1, 20)
        )
        await create_test_expense(
            db_session, owner_id=user.id, expense_date=date(2025, 1, 5)
        )

        expenses = await crud.expense.get_multi_by_date(
            db_session,
            owner_id=user.id,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
        )
        assert len(expenses) == 2
        assert expenses[0].date <= expenses[1].date


class TestCRUDExpenseRemove:
    """Tests for expense removal operations."""

    async def test_remove_single(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_rm@example.com")
        expense = await create_test_expense(db_session, owner_id=user.id)
        removed = await crud.expense.remove(db_session, id=expense.id)
        assert removed.id == expense.id

        fetched = await crud.expense.get(db_session, id=expense.id)
        assert fetched is None

    async def test_remove_multi(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_rm_multi@example.com")
        e1 = await create_test_expense(db_session, owner_id=user.id, description="rm1")
        e2 = await create_test_expense(db_session, owner_id=user.id, description="rm2")
        e3 = await create_test_expense(db_session, owner_id=user.id, description="rm3")

        removed = await crud.expense.remove_multi(
            db_session, ids=[e1.id, e2.id]
        )
        assert len(removed) == 2

        remaining = await crud.expense.get_multi_by_owner(
            db_session, owner_id=user.id
        )
        assert len(remaining) == 1
        assert remaining[0].id == e3.id
