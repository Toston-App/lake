"""update_profile, convert_transaction, paged lists and investment reads, on a real DB."""

# ruff: noqa: ARG001
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.ai.confirmation import build_pending_preview, format_confirmation_message
from app.ai.dispatch import execute_tool
from app.models import Account, Expense, Income, Place, Transfer, User
from app.services import transactions as transaction_service
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_category,
    create_test_expense,
    create_test_holding,
    create_test_user,
)


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "app.services.transactions.invalidate_user_cache",
        "app.services.profile.invalidate_user_cache",
    ):
        monkeypatch.setattr(target, AsyncMock(return_value=True))


async def _run(db: AsyncSession, user: User, name: str, args: dict) -> dict:
    result, _ = await execute_tool(name, {"response_language": "en", **args}, db, user)
    return result


# -- profile -----------------------------------------------------------------------


async def test_profile_reports_currency_and_default_account(
    db_session: AsyncSession, test_user: User
) -> None:
    test_user.country = "es-MX"
    result, _ = await execute_tool("get_user_profile", {}, db_session, test_user)
    assert result["data"]["currency"] == "MXN"
    assert result["data"]["locale"] == "es-MX"


async def test_update_profile(db_session: AsyncSession, test_user: User) -> None:
    account = await create_test_account(db_session, owner_id=test_user.id)

    result = await _run(
        db_session,
        test_user,
        "update_profile",
        {"name": "Ana", "currency": "eur", "default_account_id": account.id},
    )

    assert result["success"], result
    assert result["data"]["currency"] == "EUR"
    await db_session.refresh(test_user)
    assert (test_user.name, test_user.country) == ("Ana", "de-DE")
    assert test_user.default_account_id == account.id


async def test_ars_is_stored_as_the_locale_the_web_app_reads(
    db_session: AsyncSession, test_user: User
) -> None:
    result = await _run(db_session, test_user, "update_profile", {"currency": "ARS"})
    assert result["data"]["currency"] == "ARS"
    assert test_user.country == "es-AR"


@pytest.mark.parametrize(
    "args",
    [{"currency": "XYZ"}, {"name": " "}, {"default_account_id": "foreign"}, {}],
    ids=["unknown-currency", "blank-name", "foreign-account", "nothing"],
)
async def test_update_profile_refuses(
    db_session: AsyncSession, test_user: User, args: dict
) -> None:
    if args.get("default_account_id") == "foreign":
        other = await create_test_user(db_session, email="o@example.com")
        account = await create_test_account(db_session, owner_id=other.id)
        args = {"default_account_id": account.id}

    result = await _run(db_session, test_user, "update_profile", args)

    assert result["success"] is False


async def test_update_profile_preview(
    db_session: AsyncSession, test_user: User
) -> None:
    preview = await build_pending_preview(
        db_session, test_user, "update_profile", {"currency": "EUR"}
    )
    assert preview["changes"] == {"currency": "EUR"}
    assert "your profile" in format_confirmation_message("update_profile", preview)


# -- convert_transaction -----------------------------------------------------------


async def _balance(db: AsyncSession, account_id: int) -> float:
    account = await db.get(Account, account_id, populate_existing=True)
    return round(account.current_balance, 2)


async def test_expense_becomes_a_transfer(
    db_session: AsyncSession, test_user: User
) -> None:
    checking = await create_test_account(db_session, owner_id=test_user.id, name="A")
    savings = await create_test_account(db_session, owner_id=test_user.id, name="B")
    created = await _run(
        db_session,
        test_user,
        "create_expense",
        {"amount": 40, "date": "2026-10-03", "account_id": checking.id},
    )
    expense_id = created["data"]["id"]
    assert await _balance(db_session, checking.id) == -40

    args = {
        "from_type": "expense",
        "id": expense_id,
        "to_type": "transfer",
        "to_acc": savings.id,
    }
    preview = await build_pending_preview(
        db_session, test_user, "convert_transaction", args
    )
    assert preview["fields"]["from_acc"] == checking.id
    assert preview["arguments"]["from_account"] == "A"
    assert preview["arguments"]["to_account"] == "B"
    assert preview["arguments"]["to_type"] == "transfer"
    assert "into a transfer" in format_confirmation_message(
        "convert_transaction", preview
    )

    result = await _run(db_session, test_user, "convert_transaction", args)

    assert result["success"], result
    assert await db_session.get(Expense, expense_id) is None
    transfer = await db_session.get(Transfer, result["data"]["record"]["id"])
    assert (transfer.amount, transfer.from_acc, transfer.to_acc) == (
        40,
        checking.id,
        savings.id,
    )
    assert str(transfer.date) == "2026-10-03"
    assert await _balance(db_session, checking.id) == -40
    assert await _balance(db_session, savings.id) == 40


async def test_expense_becomes_an_income_without_its_category(
    db_session: AsyncSession, test_user: User
) -> None:
    account = await create_test_account(db_session, owner_id=test_user.id)
    category = await create_test_category(db_session, owner_id=test_user.id)
    expense = await create_test_expense(
        db_session,
        owner_id=test_user.id,
        amount=25,
        account_id=account.id,
        category_id=category.id,
        expense_date=date(2026, 10, 1),
    )

    result = await _run(
        db_session,
        test_user,
        "convert_transaction",
        {"from_type": "expense", "id": expense.id, "to_type": "income"},
    )

    assert result["success"], result
    income = await db_session.get(Income, result["data"]["record"]["id"])
    assert (income.amount, income.account_id) == (25, account.id)
    assert income.made_from == "Chat"


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"to_type": "transfer"}, "pass to_acc"),
        ({"to_type": "expense"}, "already has that type"),
        ({"to_type": "income", "category_id": 1}, "Incomes have no category"),
    ],
)
async def test_conversion_refusals(
    db_session: AsyncSession, test_user: User, args: dict, error: str
) -> None:
    expense = await create_test_expense(db_session, owner_id=test_user.id)

    result = await _run(
        db_session,
        test_user,
        "convert_transaction",
        {"from_type": "expense", "id": expense.id, **args},
    )

    assert result["success"] is False
    assert error in result["error"]
    assert await db_session.get(Expense, expense.id) is not None


async def test_recurring_and_foreign_transactions_are_refused(
    db_session: AsyncSession, test_user: User
) -> None:
    other = await create_test_user(db_session, email="x@example.com")
    foreign = await create_test_expense(db_session, owner_id=other.id)
    account = await create_test_account(db_session, owner_id=test_user.id)
    series = await _run(
        db_session,
        test_user,
        "create_recurring",
        {
            "type": "expense",
            "amount": 9,
            "frequency": "monthly",
            "start_date": date.today().isoformat(),
            "account_id": account.id,
        },
    )
    generated = (
        await db_session.execute(
            select(Expense).where(Expense.recurring_id == series["data"]["id"])
        )
    ).scalar_one()

    for expense_id in (foreign.id, generated.id):
        result = await _run(
            db_session,
            test_user,
            "convert_transaction",
            {"from_type": "expense", "id": expense_id, "to_type": "income"},
        )
        assert result["success"] is False


async def test_failed_delete_removes_the_new_record(
    db_session: AsyncSession, test_user: User, monkeypatch
) -> None:
    expense = await create_test_expense(db_session, owner_id=test_user.id)
    monkeypatch.setattr(
        transaction_service, "delete_expense", AsyncMock(side_effect=RuntimeError("x"))
    )

    result = await _run(
        db_session,
        test_user,
        "convert_transaction",
        {"from_type": "expense", "id": expense.id, "to_type": "income"},
    )

    assert result["success"] is False
    assert await db_session.get(Expense, expense.id) is not None
    incomes = await db_session.execute(
        select(Income).where(Income.owner_id == test_user.id)
    )
    assert incomes.scalars().all() == []


# -- paged lists -------------------------------------------------------------------


async def test_places_are_paged(db_session: AsyncSession, test_user: User) -> None:
    db_session.add_all(
        Place(name=f"Place {i:03}", owner_id=test_user.id) for i in range(101)
    )
    await db_session.commit()

    first, _ = await execute_tool("list_places", {}, db_session, test_user)
    second, _ = await execute_tool("list_places", {"page": 2}, db_session, test_user)

    assert len(first["data"]) == 100
    assert (first["has_more"], first["next_page"]) == (True, 2)
    assert len(second["data"]) == 1
    assert (second["has_more"], second["next_page"]) == (False, None)


# -- investments -------------------------------------------------------------------


async def test_investments_need_the_feature(
    db_session: AsyncSession, test_user: User
) -> None:
    result, _ = await execute_tool("get_portfolio_summary", {}, db_session, test_user)
    assert result["success"] is False
    assert "not enabled" in result["error"]


async def test_portfolio_summary_and_holdings(
    db_session: AsyncSession, test_user: User, enable_investments
) -> None:
    test_user.country = "en-US"
    test_user.balance_total = 50
    account = await create_test_account(db_session, owner_id=test_user.id)
    asset = await create_test_asset(db_session, symbol="TST")
    holding = await create_test_holding(
        db_session,
        owner_id=test_user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal("2"),
        avg_cost_basis=Decimal("40"),
    )
    holding.current_value = holding.current_value_usd = Decimal("100")
    holding.current_value_mxn = Decimal("1800")
    db_session.add_all([holding, test_user])
    await db_session.commit()

    summary, _ = await execute_tool("get_portfolio_summary", {}, db_session, test_user)
    holdings, _ = await execute_tool("list_holdings", {}, db_session, test_user)
    transactions, _ = await execute_tool(
        "list_investment_transactions", {}, db_session, test_user
    )

    data = summary["data"]
    assert data["total_value_usd"] == pytest.approx(100)
    assert data["cash_balance"] == {"amount": 50, "currency": "USD"}
    assert data["net_worth"] == {"amount": 150, "currency": "USD"}
    [row] = holdings["data"]
    assert row["symbol"] == "TST"
    assert holdings["has_more"] is False
    assert transactions["data"] == []


async def test_net_worth_is_omitted_for_other_currencies(
    db_session: AsyncSession, test_user: User, enable_investments
) -> None:
    test_user.country = "de-DE"

    summary, _ = await execute_tool("get_portfolio_summary", {}, db_session, test_user)

    assert summary["data"]["net_worth"] is None
    assert summary["data"]["cash_balance"]["currency"] == "EUR"


async def test_broken_session_is_rolled_back_before_cleanup(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services import transaction_conversion as conversion
    from sqlalchemy.exc import DBAPIError

    source = SimpleNamespace(
        amount=5,
        date=date(2026, 10, 1),
        description="x",
        account_id=1,
        place_id=None,
        recurring_id=None,
    )
    monkeypatch.setattr(conversion, "load_source", AsyncMock(return_value=source))
    created = SimpleNamespace(id=42)
    monkeypatch.setattr(
        transaction_service, "create_income", AsyncMock(return_value=created)
    )
    monkeypatch.setattr(
        transaction_service,
        "delete_expense",
        AsyncMock(side_effect=DBAPIError("DELETE", {}, Exception("boom"))),
    )
    delete_income = AsyncMock()
    monkeypatch.setattr(transaction_service, "delete_income", delete_income)
    db = SimpleNamespace(is_active=False, rollback=AsyncMock(), refresh=AsyncMock())
    user = SimpleNamespace(id=1)

    with pytest.raises(DBAPIError):
        await conversion.convert_transaction(
            db,
            user,
            from_type=conversion.EXPENSE,
            transaction_id=7,
            to_type=conversion.INCOME,
            targets=conversion.ConversionTargets(),
            made_from="MCP",
        )

    db.rollback.assert_awaited_once()
    db.refresh.assert_awaited_once_with(user)
    delete_income.assert_awaited_once_with(db, user, 42)


async def _holding(
    db: AsyncSession, user: User, quantity: str = "2", symbol: str = "REC"
):
    account = await create_test_account(db, owner_id=user.id)
    asset = await create_test_asset(db, symbol=symbol)
    return await create_test_holding(
        db,
        owner_id=user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=Decimal(quantity),
        avg_cost_basis=Decimal("10"),
    )


async def test_record_investment_buy_updates_the_holding(
    db_session: AsyncSession, test_user: User, enable_investments
) -> None:
    holding = await _holding(db_session, test_user)
    args = {
        "holding_id": holding.id,
        "transaction_type": "buy",
        "quantity": 3,
        "price_per_unit": 12,
        "fees": 1,
    }

    preview = await build_pending_preview(
        db_session, test_user, "record_investment_transaction", args
    )
    message = format_confirmation_message("record_investment_transaction", preview)
    assert preview["display_name"] == "REC"
    assert message.startswith("Review this buy of **3 REC** at 12")

    result = await _run(db_session, test_user, "record_investment_transaction", args)

    assert result["success"], result
    await db_session.refresh(holding)
    assert holding.quantity == Decimal("5")


async def test_record_investment_refuses_overselling_and_foreign_holdings(
    db_session: AsyncSession, test_user: User, enable_investments
) -> None:
    holding = await _holding(db_session, test_user, quantity="1")
    other = await create_test_user(db_session, email="inv@example.com")
    foreign = await _holding(db_session, other, symbol="OTH")

    oversell = await _run(
        db_session,
        test_user,
        "record_investment_transaction",
        {
            "holding_id": holding.id,
            "transaction_type": "sell",
            "quantity": 5,
            "price_per_unit": 1,
        },
    )
    theirs = await _run(
        db_session,
        test_user,
        "record_investment_transaction",
        {
            "holding_id": foreign.id,
            "transaction_type": "buy",
            "quantity": 1,
            "price_per_unit": 1,
        },
    )

    assert oversell["success"] is False
    assert "Only 1" in oversell["error"]
    assert theirs["success"] is False
    assert "not found" in theirs["error"].lower()
