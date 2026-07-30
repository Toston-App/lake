from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.ai import agent, chat_tools
from app.ai.chat_tools import (
    _bootstrap_account,
    format_confirmation_message,
    format_confirmation_result,
    requires_confirmation,
)
from app.ai.tools import (
    BulkCreateArgs,
    BulkDeleteArgs,
    CreateBalanceAdjustmentArgs,
    CreateExpenseArgs,
    CreateTransferArgs,
    DeleteTransactionArgs,
    GetPeriodArgs,
    ManagePlaceArgs,
    PlaceInput,
    UpdateTransactionArgs,
)
from app.api.api_v1.endpoints.chat import _resolve_client_context
from app.api.deps import DateFilterType
from app.schemas.dashboard import Balance, ChartsResponse, SummaryResponse


def _ctx(*, local_date: date = date(2026, 7, 28)) -> SimpleNamespace:
    return SimpleNamespace(
        deps=SimpleNamespace(
            db=SimpleNamespace(),
            user=SimpleNamespace(id=1),
            conversation_id="conversation-1",
            turn_id=7,
            local_date=local_date,
            had_pending_confirmation=False,
        )
    )


async def test_expense_create_saves_immediately_with_local_date(monkeypatch) -> None:
    create = AsyncMock(return_value=SimpleNamespace(id=12))
    store = AsyncMock()
    monkeypatch.setattr(agent.transaction_service, "create_expense", create)
    monkeypatch.setattr(agent, "store_pending_action", store)

    result = await agent.create_expense(_ctx(), CreateExpenseArgs(amount=20))

    assert result == "Expense created successfully."
    created = create.await_args.args[2]
    assert created.date == "2026-07-28"
    assert created.made_from == "Chat"
    store.assert_not_awaited()


async def test_period_summary_gives_model_the_same_values_as_ui(monkeypatch) -> None:
    summary = SummaryResponse(
        country="Mexico",
        balance=Balance(total=-1789.04, income=0, outcome=1789.04),
        period_income=0,
        period_expenses=1157.89,
        period_net=-1157.89,
    )
    fetch = AsyncMock(return_value=summary)
    monkeypatch.setattr(agent, "fetch_period_summary", fetch)
    ctx = _ctx()

    result = await agent.get_period_summary(
        ctx,
        GetPeriodArgs(date_filter_type="week", date="2026-07-27"),
    )

    assert isinstance(result.return_value, dict)
    assert result.return_value == result.metadata[0].data == summary.model_dump()
    assert result.return_value["period_expenses"] == 1157.89
    assert result.return_value["period_net"] == -1157.89
    assert result.return_value["balance"]["total"] == -1789.04
    fetch.assert_awaited_once_with(
        ctx.deps.db,
        ctx.deps.user,
        DateFilterType.week,
        "2026-07-27",
        None,
    )


async def test_period_charts_give_model_the_same_values_as_ui(monkeypatch) -> None:
    charts = ChartsResponse(
        net={
            "series": [{"name": "Net", "data": [-100.0, 50.0]}],
            "xAxis": ["Monday", "Tuesday"],
            "summary": {
                "current": -50.0,
                "previous": -20.0,
                "change": 30.0,
                "changePercent": 150.0,
                "isPositive": False,
            },
        },
        income_vs_expense={
            "series": [
                {"name": "Expenses", "data": [100.0, 75.0]},
                {"name": "Incomes", "data": [0.0, 125.0]},
            ],
            "xAxis": ["Monday", "Tuesday"],
        },
        categories={
            "categories": [
                {"name": "Food", "value": 125.0},
                {"name": "Transport", "value": 50.0},
            ]
        },
    )
    fetch = AsyncMock(return_value=charts)
    monkeypatch.setattr(agent, "fetch_period_charts", fetch)
    ctx = _ctx()

    result = await agent.get_period_charts(
        ctx,
        GetPeriodArgs(date_filter_type="week", date="2026-07-27"),
    )

    assert isinstance(result.return_value, dict)
    assert result.return_value == result.metadata[0].data == charts.model_dump()
    assert result.return_value["net"]["series"][0]["data"] == [-100.0, 50.0]
    assert result.return_value["income_vs_expense"]["series"][0] == {
        "name": "Expenses",
        "data": [100.0, 75.0],
    }
    assert result.return_value["categories"]["categories"][0] == {
        "name": "Food",
        "value": 125.0,
    }
    fetch.assert_awaited_once_with(
        ctx.deps.db,
        ctx.deps.user,
        DateFilterType.week,
        "2026-07-27",
        None,
    )


async def test_transfer_creates_one_localized_pending_action(monkeypatch) -> None:
    preview = {
        "operation": "create",
        "transaction_type": "transfer",
        "fields": {"amount": 50},
        "labels": {"from_acc": "Checking"},
    }
    store = AsyncMock(return_value=True)
    monkeypatch.setattr(agent, "build_pending_preview", AsyncMock(return_value=preview))
    monkeypatch.setattr(agent, "store_pending_action", store)

    result = await agent.create_transfer(
        _ctx(),
        CreateTransferArgs(
            amount=50,
            from_acc=1,
            to_acc=2,
            response_language="es",
        ),
    )

    assert store.await_count == 1
    stored = store.await_args.kwargs
    assert stored["response_language"] == "es"
    assert stored["tool_args"]["date"] == "2026-07-28"
    assert result.metadata[0].data["response_language"] == "es"
    assert "Revisa" in result.metadata[0].data["message"]


async def test_updates_deletes_balance_and_bulk_operations_are_deferred(
    monkeypatch,
) -> None:
    defer = AsyncMock(return_value="pending")
    monkeypatch.setattr(agent, "_defer_for_confirmation", defer)
    ctx = _ctx()

    await agent.update_expense(
        ctx, UpdateTransactionArgs(id=1, data={"amount": 10}, response_language="en")
    )
    await agent.delete_income(
        ctx, DeleteTransactionArgs(id=2, response_language="en")
    )
    await agent.manage_place(
        ctx,
        ManagePlaceArgs(
            action="update",
            id=3,
            data=PlaceInput(name="Shop"),
            response_language="en",
        ),
    )
    await agent.create_balance_adjustment(
        ctx,
        CreateBalanceAdjustmentArgs(
            account_id=1, new_balance=100, response_language="en"
        ),
    )
    await agent.bulk_create_transactions(
        ctx, BulkCreateArgs(expenses=[], incomes=[], response_language="en")
    )
    await agent.bulk_delete_transactions(
        ctx, BulkDeleteArgs(expenses=[1], response_language="en")
    )

    assert [call.args[1] for call in defer.await_args_list] == [
        "update_expense",
        "delete_income",
        "manage_place",
        "create_balance_adjustment",
        "bulk_create_transactions",
        "bulk_delete_transactions",
    ]


def test_confirmation_copy_is_localized_and_hides_record_id() -> None:
    preview = {
        "operation": "update",
        "transaction_type": "expense",
        "record_id": 987,
    }

    english = format_confirmation_message("update_expense", preview, "en")
    spanish = format_confirmation_message("update_expense", preview, "es")

    assert "987" not in english
    assert "987" not in spanish
    assert "Review" in english
    assert "Revisa" in spanish
    assert format_confirmation_result(success=True, response_language="es").startswith(
        "Listo"
    )


def test_bootstrap_account_excludes_balance() -> None:
    account = SimpleNamespace(
        id=1, name="Checking", type="bank", current_balance=1234.56
    )

    assert _bootstrap_account(account) == {
        "id": 1,
        "name": "Checking",
        "type": "bank",
    }


def test_client_context_validates_timezone_and_falls_back_to_utc() -> None:
    locale, zone, _ = _resolve_client_context(
        {"client_context": {"locale": "es-MX", "time_zone": "Invalid/Zone"}}
    )

    assert locale == "es-MX"
    assert zone == "UTC"


def test_only_risky_writes_require_confirmation() -> None:
    assert not requires_confirmation("create_expense", {})
    assert not requires_confirmation("manage_account", {"action": "create"})
    assert requires_confirmation("manage_account", {"action": "update"})
    assert requires_confirmation("create_transfer", {})
    assert requires_confirmation("bulk_create_transactions", {})
    assert requires_confirmation("delete_expense", {})


async def test_transaction_choices_use_human_names(monkeypatch) -> None:
    accounts = [SimpleNamespace(id=1, name="Checking")]
    subcategory = SimpleNamespace(id=4, name="Dining", category_id=3)
    categories = [
        SimpleNamespace(
            id=3,
            name="Food",
            is_income=False,
            subcategories=[subcategory],
        )
    ]
    places = [SimpleNamespace(id=5, name="Cafe")]
    monkeypatch.setattr(
        chat_tools.account_service,
        "list_accounts",
        AsyncMock(return_value=accounts),
    )
    monkeypatch.setattr(
        chat_tools.catalog_service,
        "list_categories",
        AsyncMock(return_value=categories),
    )
    monkeypatch.setattr(
        chat_tools.catalog_service,
        "list_places",
        AsyncMock(return_value=places),
    )

    choices = await chat_tools._transaction_field_choices(
        SimpleNamespace(), SimpleNamespace(id=1), "expense"
    )

    assert choices["account_id"] == [{"value": 1, "label": "Checking"}]
    assert choices["category_id"] == [{"value": 3, "label": "Food"}]
    assert choices["subcategory_id"] == [
        {"value": 4, "label": "Dining", "parent_value": 3}
    ]
    assert choices["place_id"] == [{"value": 5, "label": "Cafe"}]
