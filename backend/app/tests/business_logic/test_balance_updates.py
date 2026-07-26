"""
End-to-end tests for balance update flows across the full chain:
  User <-> Account <-> Category/Subcategory <-> Expense/Income/Transfer

These tests use the CRUD layer (not the API) to verify that creating
expenses, incomes, and transfers correctly cascades balance updates
through accounts, users, categories, and subcategories.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.schemas.expense import ExpenseCreate
from app.schemas.income import IncomeCreate
from app.schemas.transfer import TransferCreate
from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_subcategory,
    create_test_user,
)


class TestExpenseBalanceFlow:
    """Verify that creating an expense updates user, account, category, and subcategory."""

    async def test_expense_updates_user_balance(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_user1@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=1000.0)

        expense_in = ExpenseCreate(amount=250.0, date="2025-06-15", account_id=account.id)
        await crud.expense.create_with_owner(db=db_session, obj_in=expense_in, owner_id=user.id)

        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == -250.0
        assert updated_user.balance_outcome == 250.0
        assert updated_user.balance_income == 0.0

    async def test_expense_updates_account_balance(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_user2@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=1000.0)

        expense_in = ExpenseCreate(amount=300.0, date="2025-06-15", account_id=account.id)
        await crud.expense.create_with_owner(db=db_session, obj_in=expense_in, owner_id=user.id)

        updated_account = await crud.account.get(db_session, id=account.id)
        assert updated_account.current_balance == 700.0  # 1000 - 300
        assert updated_account.total_expenses == 300.0

    async def test_expense_updates_category_total(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_user3@test.com")
        account = await create_test_account(db_session, owner_id=user.id)
        category = await create_test_category(db_session, owner_id=user.id, name="Food")

        expense_in = ExpenseCreate(
            amount=50.0, date="2025-06-15",
            account_id=account.id, category_id=category.id,
        )
        await crud.expense.create_with_owner(db=db_session, obj_in=expense_in, owner_id=user.id)

        updated_cat = await crud.category.get(db_session, id=category.id)
        assert updated_cat.total == 50.0

    async def test_expense_updates_subcategory_total(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="exp_user4@test.com")
        account = await create_test_account(db_session, owner_id=user.id)
        category = await create_test_category(db_session, owner_id=user.id, name="Food")
        subcategory = await create_test_subcategory(
            db_session, owner_id=user.id, category_id=category.id, name="Groceries"
        )

        expense_in = ExpenseCreate(
            amount=75.0, date="2025-06-15",
            account_id=account.id,
            category_id=category.id,
            subcategory_id=subcategory.id,
        )
        await crud.expense.create_with_owner(db=db_session, obj_in=expense_in, owner_id=user.id)

        updated_sub = await crud.subcategory.get(db_session, id=subcategory.id)
        assert updated_sub.total == 75.0

    async def test_expense_full_chain(self, db_session: AsyncSession):
        """Create an expense and verify ALL balance updates in one test."""
        user = await create_test_user(db_session, email="exp_full@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=2000.0)
        category = await create_test_category(db_session, owner_id=user.id, name="Transport")
        subcategory = await create_test_subcategory(
            db_session, owner_id=user.id, category_id=category.id, name="Gas"
        )

        expense_in = ExpenseCreate(
            amount=120.50, date="2025-06-15",
            account_id=account.id,
            category_id=category.id,
            subcategory_id=subcategory.id,
        )
        expense = await crud.expense.create_with_owner(
            db=db_session, obj_in=expense_in, owner_id=user.id
        )

        # Verify expense itself
        assert expense.amount == 120.50
        assert expense.owner_id == user.id

        # Verify user balance
        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == -120.50
        assert updated_user.balance_outcome == 120.50

        # Verify account balance
        updated_account = await crud.account.get(db_session, id=account.id)
        assert updated_account.current_balance == 2000.0 - 120.50
        assert updated_account.total_expenses == 120.50

        # Verify category total
        updated_cat = await crud.category.get(db_session, id=category.id)
        assert updated_cat.total == 120.50

        # Verify subcategory total
        updated_sub = await crud.subcategory.get(db_session, id=subcategory.id)
        assert updated_sub.total == 120.50


class TestIncomeBalanceFlow:
    """Verify that creating an income updates user, account, subcategory, AND category."""

    async def test_income_updates_user_balance(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_user1@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=500.0)

        income_in = IncomeCreate(amount=1000.0, date="2025-06-15", account_id=account.id)
        await crud.income.create_with_owner(db=db_session, obj_in=income_in, owner_id=user.id)

        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == 1000.0
        assert updated_user.balance_income == 1000.0
        assert updated_user.balance_outcome == 0.0

    async def test_income_updates_account_balance(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="inc_user2@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=500.0)

        income_in = IncomeCreate(amount=800.0, date="2025-06-15", account_id=account.id)
        await crud.income.create_with_owner(db=db_session, obj_in=income_in, owner_id=user.id)

        updated_account = await crud.account.get(db_session, id=account.id)
        assert updated_account.current_balance == 1300.0  # 500 + 800
        assert updated_account.total_incomes == 800.0

    async def test_income_updates_subcategory_and_category(self, db_session: AsyncSession):
        """Income should update BOTH subcategory.total AND its parent category.total."""
        user = await create_test_user(db_session, email="inc_user3@test.com")
        account = await create_test_account(db_session, owner_id=user.id)
        category = await create_test_category(
            db_session, owner_id=user.id, name="Salary", is_income=True
        )
        subcategory = await create_test_subcategory(
            db_session, owner_id=user.id, category_id=category.id, name="Main Job"
        )

        income_in = IncomeCreate(
            amount=3000.0, date="2025-06-15",
            account_id=account.id,
            subcategory_id=subcategory.id,
        )
        await crud.income.create_with_owner(db=db_session, obj_in=income_in, owner_id=user.id)

        updated_sub = await crud.subcategory.get(db_session, id=subcategory.id)
        assert updated_sub.total == 3000.0

        updated_cat = await crud.category.get(db_session, id=category.id)
        assert updated_cat.total == 3000.0

    async def test_income_full_chain(self, db_session: AsyncSession):
        """Create an income and verify ALL balance updates in one test."""
        user = await create_test_user(db_session, email="inc_full@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=100.0)
        category = await create_test_category(
            db_session, owner_id=user.id, name="Freelance", is_income=True
        )
        subcategory = await create_test_subcategory(
            db_session, owner_id=user.id, category_id=category.id, name="Projects"
        )

        income_in = IncomeCreate(
            amount=2500.0, date="2025-07-01",
            account_id=account.id,
            subcategory_id=subcategory.id,
        )
        income = await crud.income.create_with_owner(
            db=db_session, obj_in=income_in, owner_id=user.id
        )

        assert income.amount == 2500.0

        # User balance
        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == 2500.0
        assert updated_user.balance_income == 2500.0

        # Account balance
        updated_account = await crud.account.get(db_session, id=account.id)
        assert updated_account.current_balance == 2600.0  # 100 + 2500
        assert updated_account.total_incomes == 2500.0

        # Category total
        updated_cat = await crud.category.get(db_session, id=category.id)
        assert updated_cat.total == 2500.0

        # Subcategory total
        updated_sub = await crud.subcategory.get(db_session, id=subcategory.id)
        assert updated_sub.total == 2500.0


class TestTransferBalanceFlow:
    """Verify that transfers update both from and to accounts correctly."""

    async def test_transfer_updates_from_account(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="xfer_user1@test.com")
        from_acc = await create_test_account(
            db_session, owner_id=user.id, name="Checking", initial_balance=5000.0
        )
        to_acc = await create_test_account(
            db_session, owner_id=user.id, name="Savings", initial_balance=1000.0
        )

        transfer_in = TransferCreate(
            amount=500.0, from_acc=from_acc.id, to_acc=to_acc.id, date="2025-06-15"
        )
        await crud.transfer.create_with_owner(
            db=db_session, obj_in=transfer_in, owner_id=user.id
        )

        updated_from = await crud.account.get(db_session, id=from_acc.id)
        assert updated_from.current_balance == 4500.0  # 5000 - 500
        assert updated_from.total_transfers_out == 500.0

    async def test_transfer_updates_to_account(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="xfer_user2@test.com")
        from_acc = await create_test_account(
            db_session, owner_id=user.id, name="Checking", initial_balance=5000.0
        )
        to_acc = await create_test_account(
            db_session, owner_id=user.id, name="Savings", initial_balance=1000.0
        )

        transfer_in = TransferCreate(
            amount=500.0, from_acc=from_acc.id, to_acc=to_acc.id, date="2025-06-15"
        )
        await crud.transfer.create_with_owner(
            db=db_session, obj_in=transfer_in, owner_id=user.id
        )

        updated_to = await crud.account.get(db_session, id=to_acc.id)
        assert updated_to.current_balance == 1500.0  # 1000 + 500
        assert updated_to.total_transfers_in == 500.0

    async def test_transfer_full_chain(self, db_session: AsyncSession):
        """Transfer should update both accounts. User balance is NOT affected by transfers."""
        user = await create_test_user(db_session, email="xfer_full@test.com")
        from_acc = await create_test_account(
            db_session, owner_id=user.id, name="Checking", initial_balance=3000.0
        )
        to_acc = await create_test_account(
            db_session, owner_id=user.id, name="Savings", initial_balance=500.0
        )

        transfer_in = TransferCreate(
            amount=750.0, from_acc=from_acc.id, to_acc=to_acc.id, date="2025-06-15"
        )
        transfer = await crud.transfer.create_with_owner(
            db=db_session, obj_in=transfer_in, owner_id=user.id
        )

        assert transfer.amount == 750.0

        # From account
        updated_from = await crud.account.get(db_session, id=from_acc.id)
        assert updated_from.current_balance == 2250.0
        assert updated_from.total_transfers_out == 750.0
        assert updated_from.total_transfers_in == 0.0

        # To account
        updated_to = await crud.account.get(db_session, id=to_acc.id)
        assert updated_to.current_balance == 1250.0
        assert updated_to.total_transfers_in == 750.0
        assert updated_to.total_transfers_out == 0.0

        # User balance should be unchanged by transfers
        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_outcome == 0.0
        assert updated_user.balance_income == 0.0


class TestMultipleOperationsAccumulate:
    """Verify that multiple operations accumulate balances correctly."""

    async def test_multiple_expenses_accumulate(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="multi_exp@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=5000.0)

        amounts = [100.0, 250.0, 75.50]
        for amount in amounts:
            expense_in = ExpenseCreate(amount=amount, date="2025-06-15", account_id=account.id)
            await crud.expense.create_with_owner(db=db_session, obj_in=expense_in, owner_id=user.id)

        total_spent = sum(amounts)

        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == pytest.approx(-total_spent)
        assert updated_user.balance_outcome == pytest.approx(total_spent)

        updated_account = await crud.account.get(db_session, id=account.id)
        assert updated_account.current_balance == pytest.approx(5000.0 - total_spent)
        assert updated_account.total_expenses == pytest.approx(total_spent)

    async def test_multiple_incomes_accumulate(self, db_session: AsyncSession):
        user = await create_test_user(db_session, email="multi_inc@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=100.0)

        amounts = [500.0, 1200.0, 350.0]
        for amount in amounts:
            income_in = IncomeCreate(amount=amount, date="2025-06-15", account_id=account.id)
            await crud.income.create_with_owner(db=db_session, obj_in=income_in, owner_id=user.id)

        total_earned = sum(amounts)

        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == pytest.approx(total_earned)
        assert updated_user.balance_income == pytest.approx(total_earned)

        updated_account = await crud.account.get(db_session, id=account.id)
        assert updated_account.current_balance == pytest.approx(100.0 + total_earned)
        assert updated_account.total_incomes == pytest.approx(total_earned)

    async def test_mixed_expenses_and_incomes(self, db_session: AsyncSession):
        """Verify that mixed operations correctly update the user's net balance."""
        user = await create_test_user(db_session, email="mixed@test.com")
        account = await create_test_account(db_session, owner_id=user.id, initial_balance=1000.0)

        # Add income
        income_in = IncomeCreate(amount=2000.0, date="2025-06-15", account_id=account.id)
        await crud.income.create_with_owner(db=db_session, obj_in=income_in, owner_id=user.id)

        # Add expense
        expense_in = ExpenseCreate(amount=800.0, date="2025-06-16", account_id=account.id)
        await crud.expense.create_with_owner(db=db_session, obj_in=expense_in, owner_id=user.id)

        # Add another income
        income_in2 = IncomeCreate(amount=500.0, date="2025-06-17", account_id=account.id)
        await crud.income.create_with_owner(db=db_session, obj_in=income_in2, owner_id=user.id)

        updated_user = await crud.user.get(db_session, id=user.id)
        # Net: +2000 - 800 + 500 = +1700
        assert updated_user.balance_total == pytest.approx(1700.0)
        assert updated_user.balance_income == pytest.approx(2500.0)
        assert updated_user.balance_outcome == pytest.approx(800.0)

        updated_account = await crud.account.get(db_session, id=account.id)
        # Account: 1000 + 2000 - 800 + 500 = 2700
        assert updated_account.current_balance == pytest.approx(2700.0)
        assert updated_account.total_incomes == pytest.approx(2500.0)
        assert updated_account.total_expenses == pytest.approx(800.0)

    async def test_multiple_expenses_same_category(self, db_session: AsyncSession):
        """Multiple expenses in the same category should accumulate the category total."""
        user = await create_test_user(db_session, email="cat_accum@test.com")
        account = await create_test_account(db_session, owner_id=user.id)
        category = await create_test_category(db_session, owner_id=user.id, name="Food")

        for amount in [30.0, 45.0, 22.50]:
            expense_in = ExpenseCreate(
                amount=amount, date="2025-06-15",
                account_id=account.id, category_id=category.id,
            )
            await crud.expense.create_with_owner(
                db=db_session, obj_in=expense_in, owner_id=user.id
            )

        updated_cat = await crud.category.get(db_session, id=category.id)
        assert updated_cat.total == pytest.approx(97.50)

    async def test_multiple_transfers_accumulate(self, db_session: AsyncSession):
        """Multiple transfers should accumulate transfer totals on both accounts."""
        user = await create_test_user(db_session, email="multi_xfer@test.com")
        from_acc = await create_test_account(
            db_session, owner_id=user.id, name="Checking", initial_balance=10000.0
        )
        to_acc = await create_test_account(
            db_session, owner_id=user.id, name="Savings", initial_balance=0.0
        )

        amounts = [1000.0, 500.0, 250.0]
        for amount in amounts:
            transfer_in = TransferCreate(
                amount=amount, from_acc=from_acc.id, to_acc=to_acc.id, date="2025-06-15"
            )
            await crud.transfer.create_with_owner(
                db=db_session, obj_in=transfer_in, owner_id=user.id
            )

        total_transferred = sum(amounts)

        updated_from = await crud.account.get(db_session, id=from_acc.id)
        assert updated_from.current_balance == pytest.approx(10000.0 - total_transferred)
        assert updated_from.total_transfers_out == pytest.approx(total_transferred)

        updated_to = await crud.account.get(db_session, id=to_acc.id)
        assert updated_to.current_balance == pytest.approx(total_transferred)
        assert updated_to.total_transfers_in == pytest.approx(total_transferred)


class TestAccountInitialBalance:
    """Verify that creating an account with initial_balance updates user.balance_total."""

    async def test_account_with_initial_balance_updates_user(self, db_session: AsyncSession):
        from app.schemas.account import AccountCreate

        user = await create_test_user(db_session, email="acc_init@test.com")
        assert user.balance_total == 0.0

        account_in = AccountCreate(name="My Account", initial_balance=5000.0)
        account = await crud.account.create_with_owner(
            db=db_session, obj_in=account_in, owner_id=user.id
        )

        assert account.current_balance == 5000.0
        assert account.initial_balance == 5000.0

        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == 5000.0

    async def test_account_with_zero_balance_no_user_update(self, db_session: AsyncSession):
        from app.schemas.account import AccountCreate

        user = await create_test_user(db_session, email="acc_zero@test.com")
        assert user.balance_total == 0.0

        account_in = AccountCreate(name="Empty Account", initial_balance=0.0)
        await crud.account.create_with_owner(
            db=db_session, obj_in=account_in, owner_id=user.id
        )

        updated_user = await crud.user.get(db_session, id=user.id)
        assert updated_user.balance_total == 0.0
