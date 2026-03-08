"""
Helper functions for creating test data objects directly in the database.
These bypass the full CRUD create_with_owner flow to avoid side effects
(balance updates, category seeding, etc.) when setting up test fixtures.
"""
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, AccountType
from app.models.category import Category
from app.models.expense import Expense
from app.models.income import Income
from app.models.place import Place
from app.models.subcategory import Subcategory
from app.models.transfer import Transfer
from app.models.user import User
from app.core.security import get_password_hash


async def create_test_user(
    db: AsyncSession,
    *,
    email: str = "user@example.com",
    password: str = "password123",
    name: str = "Test User",
    country: str = "USD",
    is_superuser: bool = False,
    is_active: bool = True,
    balance_total: float = 0.0,
    balance_income: float = 0.0,
    balance_outcome: float = 0.0,
) -> User:
    user = User(
        email=email,
        hashed_password=get_password_hash(password),
        name=name,
        country=country,
        is_active=is_active,
        is_superuser=is_superuser,
        balance_total=balance_total,
        balance_income=balance_income,
        balance_outcome=balance_outcome,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def create_test_account(
    db: AsyncSession,
    *,
    owner_id: int,
    name: str = "Test Account",
    account_type: AccountType = AccountType.CHECKING,
    initial_balance: float = 0.0,
    current_balance: float | None = None,
    color: str = "#168FFF",
) -> Account:
    if current_balance is None:
        current_balance = initial_balance
    account = Account(
        name=name,
        type=account_type,
        initial_balance=initial_balance,
        current_balance=current_balance,
        total_expenses=0.0,
        total_incomes=0.0,
        total_transfers_in=0.0,
        total_transfers_out=0.0,
        owner_id=owner_id,
        color=color,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def create_test_category(
    db: AsyncSession,
    *,
    owner_id: int,
    name: str = "Test Category",
    color: str = "#FF5733",
    icon: str = "shopping-cart",
    is_income: bool = False,
    is_default: bool = False,
    total: float = 0.0,
) -> Category:
    category = Category(
        name=name,
        color=color,
        icon=icon,
        is_income=is_income,
        is_default=is_default,
        owner_id=owner_id,
        total=total,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


async def create_test_subcategory(
    db: AsyncSession,
    *,
    owner_id: int,
    category_id: int,
    name: str = "Test Subcategory",
    icon: str = "tag",
    is_default: bool = False,
    total: float = 0.0,
) -> Subcategory:
    subcategory = Subcategory(
        name=name,
        icon=icon,
        is_default=is_default,
        owner_id=owner_id,
        category_id=category_id,
        total=total,
    )
    db.add(subcategory)
    await db.commit()
    await db.refresh(subcategory)
    return subcategory


async def create_test_place(
    db: AsyncSession,
    *,
    owner_id: int,
    name: str = "Test Place",
    is_online: bool = False,
) -> Place:
    place = Place(
        name=name,
        is_online=is_online,
        owner_id=owner_id,
    )
    db.add(place)
    await db.commit()
    await db.refresh(place)
    return place


async def create_test_expense(
    db: AsyncSession,
    *,
    owner_id: int,
    amount: float = 100.0,
    expense_date: date | None = None,
    description: str = "Test expense",
    account_id: int | None = None,
    category_id: int | None = None,
    subcategory_id: int | None = None,
    place_id: int | None = None,
    made_from: str = "Web",
) -> Expense:
    if expense_date is None:
        expense_date = date.today()
    expense = Expense(
        amount=amount,
        date=expense_date,
        description=description,
        owner_id=owner_id,
        account_id=account_id,
        category_id=category_id,
        subcategory_id=subcategory_id,
        place_id=place_id,
        made_from=made_from,
    )
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return expense


async def create_test_income(
    db: AsyncSession,
    *,
    owner_id: int,
    amount: float = 500.0,
    income_date: date | None = None,
    description: str = "Test income",
    account_id: int | None = None,
    subcategory_id: int | None = None,
    place_id: int | None = None,
    made_from: str = "Web",
) -> Income:
    if income_date is None:
        income_date = date.today()
    income = Income(
        amount=amount,
        date=income_date,
        description=description,
        owner_id=owner_id,
        account_id=account_id,
        subcategory_id=subcategory_id,
        place_id=place_id,
        made_from=made_from,
    )
    db.add(income)
    await db.commit()
    await db.refresh(income)
    return income


async def create_test_transfer(
    db: AsyncSession,
    *,
    owner_id: int,
    from_acc: int,
    to_acc: int,
    amount: float = 200.0,
    transfer_date: date | None = None,
    description: str = "Test transfer",
) -> Transfer:
    if transfer_date is None:
        transfer_date = date.today()
    transfer = Transfer(
        amount=amount,
        date=transfer_date,
        description=description,
        from_acc=from_acc,
        to_acc=to_acc,
        owner_id=owner_id,
    )
    db.add(transfer)
    await db.commit()
    await db.refresh(transfer)
    return transfer
