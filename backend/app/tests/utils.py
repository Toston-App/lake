"""
Helper functions for creating test data objects directly in the database.
These bypass the full CRUD create_with_owner flow to avoid side effects
(balance updates, category seeding, etc.) when setting up test fixtures.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.core.security import get_password_hash
from app.models.account import Account, AccountType
from app.models.asset import (
    ASSET_TYPE_TO_CLASS,
    Asset,
    AssetClass,
    AssetType,
    Currency,
    Market,
)
from app.models.asset_price import AssetPrice
from app.models.category import Category
from app.models.expense import Expense
from app.models.holding import Holding
from app.models.income import Income
from app.models.investment_transaction import InvestmentTransaction, TransactionType
from app.models.place import Place
from app.models.subcategory import Subcategory
from app.models.transfer import Transfer
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession


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


async def create_test_asset(
    db: AsyncSession,
    *,
    symbol: str = "AAPL",
    name: str = "Apple Inc.",
    asset_type: AssetType = AssetType.STOCK,
    asset_class: AssetClass | None = None,
    currency: Currency = Currency.USD,
    market: Market = Market.NASDAQ,
    sector: str | None = "Technology",
    country: str = "US",
    coingecko_id: str | None = None,
    is_active: bool = True,
) -> Asset:
    asset = Asset(
        symbol=symbol.upper(),
        name=name,
        asset_type=asset_type,
        asset_class=asset_class or ASSET_TYPE_TO_CLASS[asset_type],
        currency=currency,
        market=market,
        sector=sector,
        country=country,
        coingecko_id=coingecko_id.lower() if coingecko_id else None,
        is_active=is_active,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


async def create_test_asset_price(
    db: AsyncSession,
    *,
    asset_id: int,
    price: Decimal = Decimal("100"),
    price_usd: Decimal | None = None,
    price_mxn: Decimal | None = None,
    currency: Currency = Currency.USD,
    fetched_at: datetime | None = None,
) -> AssetPrice:
    price_usd = price if price_usd is None else price_usd
    price_mxn = price * Decimal("18") if price_mxn is None else price_mxn
    asset_price = AssetPrice(
        asset_id=asset_id,
        price=price,
        currency=currency,
        price_usd=price_usd,
        price_mxn=price_mxn,
        fetched_at=fetched_at or datetime.now(timezone.utc),
    )
    db.add(asset_price)
    await db.commit()
    await db.refresh(asset_price)
    return asset_price


async def create_test_holding(
    db: AsyncSession,
    *,
    owner_id: int,
    account_id: int,
    asset_id: int,
    quantity: Decimal = Decimal("10"),
    avg_cost_basis: Decimal = Decimal("100"),
    cost_currency: Currency = Currency.USD,
    asset_currency: Currency = Currency.USD,
    usd_mxn_rate: Decimal = Decimal("18"),
) -> Holding:
    total_invested = quantity * avg_cost_basis
    if cost_currency == asset_currency:
        current_value = total_invested
    elif cost_currency == Currency.USD:
        current_value = total_invested * usd_mxn_rate
    else:
        current_value = total_invested / usd_mxn_rate
    current_value_usd = (
        current_value
        if asset_currency == Currency.USD
        else current_value / usd_mxn_rate
    )
    current_value_mxn = (
        current_value
        if asset_currency == Currency.MXN
        else current_value * usd_mxn_rate
    )
    holding = Holding(
        owner_id=owner_id,
        account_id=account_id,
        asset_id=asset_id,
        quantity=quantity,
        avg_cost_basis=avg_cost_basis,
        cost_currency=cost_currency,
        total_invested=total_invested,
        current_value=current_value,
        current_value_usd=current_value_usd,
        current_value_mxn=current_value_mxn,
        unrealized_gain_loss=Decimal("0"),
        unrealized_gain_loss_pct=Decimal("0"),
    )
    db.add(holding)
    await db.commit()
    await db.refresh(holding)
    return holding


async def create_test_investment_transaction(
    db: AsyncSession,
    *,
    owner_id: int,
    account_id: int,
    holding_id: int,
    transaction_type: TransactionType = TransactionType.BUY,
    quantity: Decimal = Decimal("1"),
    price_per_unit: Decimal = Decimal("100"),
    fees: Decimal = Decimal("0"),
    currency: Currency = Currency.USD,
    executed_at: datetime | None = None,
    idempotency_key: str | None = None,
    request_fingerprint: str = "test-fingerprint",
) -> InvestmentTransaction:
    gross = quantity * price_per_unit
    transaction = InvestmentTransaction(
        owner_id=owner_id,
        account_id=account_id,
        holding_id=holding_id,
        transaction_type=transaction_type,
        quantity=quantity,
        price_per_unit=price_per_unit,
        fees=fees,
        currency=currency,
        total_amount=gross - fees
        if transaction_type == TransactionType.SELL
        else gross,
        exchange_rate_to_usd=Decimal("1")
        if currency == Currency.USD
        else Decimal("0.055555555556"),
        exchange_rate_to_mxn=Decimal("18")
        if currency == Currency.USD
        else Decimal("1"),
        executed_at=executed_at or datetime.now(timezone.utc),
        idempotency_key=idempotency_key or f"test-{uuid4()}",
        request_fingerprint=request_fingerprint,
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction
