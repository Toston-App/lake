import enum
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .balance_adjustment import BalanceAdjustment  # noqa: F401
    from .expense import Expense  # noqa: F401
    from .holding import Holding  # noqa: F401
    from .income import Income  # noqa: F401
    from .investment_transaction import InvestmentTransaction  # noqa: F401
    from .transfer import Transfer  # noqa: F401
    from .user import User  # noqa: F401


class AccountType(str, enum.Enum):
    """
    1️⃣ Bank Accounts & Cash
        🏦 Checking Accounts → Day-to-day spending (e.g., "BBVA", "Bank of America")
        💰 Savings Accounts → Money set aside for future use
        🏡 Cash & Wallet → Physical money

    2️⃣ Investments
        📈 Stocks & Bonds → Investment accounts
        🪙 Crypto & Digital Assets → Bitcoin, Ethereum, etc.
        💼 Retirement Funds → 401(k), IRA, pension accounts

    3️⃣ Liabilities & Debts
        🏠 Loans & Mortgages → Housing loans, car loans, personal loans
        💳 Credit Cards → Accounts with negative balance
        📉 Overdrafts & Negative Balances → Any account with a negative current_balance

    4️⃣ Business & Side Hustles
        🏢 Business Accounts → Work-related banking
        🚀 Freelance & Side Income → PayPal, Stripe, or other sources of earnings

    5️⃣ Others
        🎟 Prepaid & Gift Cards → Stored-value accounts
        🎭 Miscellaneous → Any account that doesn’t fit above
    """

    # Bank Accounts & Cash
    CHECKING = "Checking Accounts"
    SAVINGS = "Savings Accounts"
    CASH = "Cash & Wallet"

    # Investments
    STOCKS = "Stocks & Bonds"
    CRYPTO = "Crypto & Digital Assets"
    RETIREMENT = "Retirement Funds"

    # Liabilities & Debts
    LOANS = "Loans & Mortgages"
    CREDIT_CARDS = "Credit Cards"
    OVERDRAFTS = "Overdrafts & Negative Balances"

    # Business & Side Hustles
    BUSINESS = "Business Accounts"
    FREELANCE = "Freelance & Side Income"

    # Others
    PREPAID = "Prepaid & Gift Cards"
    MISCELLANEOUS = "Miscellaneous"


class Account(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    name: str = Column(String, index=True, nullable=False)
    type: AccountType = Column(
        Enum(AccountType), index=True, nullable=False, default=AccountType.MISCELLANEOUS
    )
    color: str = Column(String, nullable=False, default="#168FFF")
    initial_balance: float = Column(Float, index=True, default=0.0)
    current_balance: float = Column(Float, index=True, default=0.0)
    total_expenses: float = Column(Float, index=True, default=0.0)
    total_incomes: float = Column(Float, index=True, default=0.0)
    total_transfers_in: float = Column(Float, index=True, default=0.0)
    total_transfers_out: float = Column(Float, index=True, default=0.0)
    total_investments_usd: Decimal = Column(
        Numeric(38, 8), index=True, nullable=False, default=Decimal("0")
    )
    total_investments_mxn: Decimal = Column(
        Numeric(38, 8), index=True, nullable=False, default=Decimal("0")
    )
    logo: str = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    owner_id = Column(Integer, ForeignKey("user.id"))
    owner: "User" = relationship(
        "User", back_populates="accounts", foreign_keys="[Account.owner_id]"
    )
    expenses: list["Expense"] = relationship("Expense", back_populates="account")
    incomes: list["Income"] = relationship("Income", back_populates="account")
    transfers_in: list["Transfer"] = relationship(
        "Transfer", foreign_keys="[Transfer.to_acc]", back_populates="account_to"
    )
    transfers_out: list["Transfer"] = relationship(
        "Transfer", foreign_keys="[Transfer.from_acc]", back_populates="account_from"
    )
    balance_adjustments: list["BalanceAdjustment"] = relationship(
        "BalanceAdjustment", back_populates="account", cascade="all, delete-orphan"
    )
    holdings: list["Holding"] = relationship(
        "Holding", back_populates="account", cascade="all, delete-orphan"
    )
    investment_transactions: list["InvestmentTransaction"] = relationship(
        "InvestmentTransaction", back_populates="account", cascade="all, delete-orphan"
    )
    import_id: int = Column(Integer, ForeignKey("import.id"))
    import_source = relationship("Import", back_populates="accounts")
