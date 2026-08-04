from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.ai import agent
from app.ai.tools import (
    GetPeriodArgs,
    ListBalanceAdjustmentsArgs,
    ReadPresentationArgs,
    SearchTransactionsArgs,
    SearchWebArgs,
)
from app.schemas.dashboard import Balance, ChartsResponse, SummaryResponse
from pydantic import ValidationError


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        deps=SimpleNamespace(db=SimpleNamespace(), user=SimpleNamespace(id=1))
    )


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (ReadPresentationArgs, {}),
        (SearchTransactionsArgs, {}),
        (GetPeriodArgs, {"date_filter_type": "month", "date": "2026-08"}),
        (ListBalanceAdjustmentsArgs, {}),
        (SearchWebArgs, {"query": "interest rates"}),
    ],
)
def test_presentation_args_default_to_model_only(factory, kwargs) -> None:
    assert factory(**kwargs).presentation == "model_only"


def test_presentation_args_accept_only_public_modes() -> None:
    assert ReadPresentationArgs(presentation="model_only").presentation == "model_only"
    assert (
        ReadPresentationArgs(presentation="user_visible").presentation
        == "user_visible"
    )
    with pytest.raises(ValidationError):
        ReadPresentationArgs(presentation="automatic")


async def test_reference_lists_are_private_unless_requested(monkeypatch) -> None:
    account = SimpleNamespace(
        id=1,
        name="Checking",
        type="checking",
        current_balance=125.5,
    )
    subcategory = SimpleNamespace(id=4, name="Dining", category_id=3)
    category = SimpleNamespace(
        id=3,
        name="Food",
        is_income=False,
        subcategories=[subcategory],
    )
    place = SimpleNamespace(id=5, name="Cafe", is_online=False)
    list_accounts = AsyncMock(return_value=[account])
    list_categories = AsyncMock(return_value=[category])
    list_places = AsyncMock(return_value=[place])
    monkeypatch.setattr(agent.account_service, "list_accounts", list_accounts)
    monkeypatch.setattr(agent.catalog_service, "list_categories", list_categories)
    monkeypatch.setattr(agent.catalog_service, "list_places", list_places)
    ctx = _ctx()

    cases = [
        (
            agent.list_accounts,
            "data-accounts-list",
            {"id": 1, "name": "Checking", "type": "checking", "current_balance": 125.5},
        ),
        (
            agent.list_categories,
            "data-categories-list",
            {
                "id": 3,
                "name": "Food",
                "is_income": False,
                "subcategories": [{"id": 4, "name": "Dining", "category_id": 3}],
            },
        ),
        (
            agent.list_places,
            "data-places-list",
            {"id": 5, "name": "Cafe", "is_online": False},
        ),
    ]
    for tool, chunk_type, item in cases:
        hidden = await tool(ctx, ReadPresentationArgs())
        visible = await tool(
            ctx,
            ReadPresentationArgs(presentation="user_visible"),
        )

        assert hidden.return_value == visible.return_value == {
            "items": [item],
            "total": 1,
        }
        assert hidden.metadata == []
        assert visible.metadata[0].type == chunk_type
        assert visible.metadata[0].data == {"items": [item]}

    assert list_accounts.await_count == 2
    assert list_categories.await_count == 2
    assert list_places.await_count == 2


async def test_transaction_search_is_private_unless_requested(monkeypatch) -> None:
    item = {
        "id": 9,
        "type": "expense",
        "amount": 42.0,
        "date": "2026-08-01",
        "description": "Dinner",
    }
    search = AsyncMock(return_value=SimpleNamespace(items=[item], total=1))
    monkeypatch.setattr(agent.transaction_service, "search_transactions", search)
    ctx = _ctx()

    hidden = await agent.search_transactions(ctx, SearchTransactionsArgs(limit=5))
    visible = await agent.search_transactions(
        ctx,
        SearchTransactionsArgs(limit=5, presentation="user_visible"),
    )

    assert hidden.return_value == visible.return_value
    assert hidden.return_value["items"][0]["id"] == 9
    assert hidden.metadata == []
    assert visible.metadata[0].type == "data-transactions-list"
    assert visible.metadata[0].data["items"] == [item]
    assert search.await_count == 2
    assert all(call.kwargs["params"].size == 5 for call in search.await_args_list)


async def test_period_reads_are_private_unless_requested(monkeypatch) -> None:
    summary = SummaryResponse(
        country="Mexico",
        balance=Balance(total=1000, income=1500, outcome=500),
        period_income=125,
        period_expenses=75,
        period_net=50,
    )
    charts = ChartsResponse(
        net={"series": [{"name": "Net", "data": [50]}], "xAxis": ["August"]},
        income_vs_expense={
            "series": [
                {"name": "Income", "data": [125]},
                {"name": "Expenses", "data": [75]},
            ],
            "xAxis": ["August"],
        },
        categories={"categories": [{"name": "Food", "value": 75}]},
    )
    fetch_summary = AsyncMock(return_value=summary)
    fetch_charts = AsyncMock(return_value=charts)
    monkeypatch.setattr(agent, "fetch_period_summary", fetch_summary)
    monkeypatch.setattr(agent, "fetch_period_charts", fetch_charts)
    ctx = _ctx()

    for tool, chunk_type, expected in [
        (agent.get_period_summary, "data-period-summary", summary.model_dump()),
        (agent.get_period_charts, "data-charts", charts.model_dump()),
    ]:
        hidden = await tool(
            ctx,
            GetPeriodArgs(date_filter_type="month", date="2026-08"),
        )
        visible = await tool(
            ctx,
            GetPeriodArgs(
                date_filter_type="month",
                date="2026-08",
                presentation="user_visible",
            ),
        )

        assert hidden.return_value == visible.return_value == expected
        assert hidden.metadata == []
        assert visible.metadata[0].type == chunk_type
        assert visible.metadata[0].data == expected

    assert fetch_summary.await_count == 2
    assert fetch_charts.await_count == 2


async def test_balance_adjustments_are_private_unless_requested(monkeypatch) -> None:
    adjustments = [
        SimpleNamespace(
            account_id=1,
            new_balance=800.0,
            adjustment_date="2026-08-01",
            description="Correction",
        )
    ]
    listing = AsyncMock(return_value=adjustments)
    monkeypatch.setattr(
        agent.balance_adjustment_service,
        "list_balance_adjustments_by_account",
        listing,
    )
    ctx = _ctx()

    hidden = await agent.list_balance_adjustments(
        ctx,
        ListBalanceAdjustmentsArgs(account_id=1),
    )
    visible = await agent.list_balance_adjustments(
        ctx,
        ListBalanceAdjustmentsArgs(
            account_id=1,
            presentation="user_visible",
        ),
    )

    assert hidden.return_value == visible.return_value
    assert hidden.return_value["items"][0]["account_id"] == 1
    assert hidden.metadata == []
    assert visible.metadata[0].type == "data-balance-adjustments-list"
    assert visible.metadata[0].data == hidden.return_value
    assert listing.await_count == 2
    assert all(call.args[2] == 1 for call in listing.await_args_list)


async def test_web_search_sources_share_the_requested_visibility(monkeypatch) -> None:
    provider_result = {
        "answer": "Rates held steady.",
        "results": [
            {
                "title": "Central bank update",
                "url": "https://example.com/rates",
                "snippet": "Policy rates were unchanged.",
            }
        ],
    }
    search = AsyncMock(return_value=provider_result)
    monkeypatch.setattr(agent, "_search_web", search)
    ctx = _ctx()

    hidden = await agent.search_web(ctx, SearchWebArgs(query="latest rates"))
    visible = await agent.search_web(
        ctx,
        SearchWebArgs(query="latest rates", presentation="user_visible"),
    )

    assert hidden.return_value == visible.return_value == {
        "answer": "Rates held steady.",
        "items": provider_result["results"],
        "total": 1,
    }
    assert hidden.metadata == []
    assert [chunk.type for chunk in visible.metadata] == [
        "data-web-sources",
        "source-url",
    ]
    assert visible.metadata[0].data == {
        "items": provider_result["results"],
        "answer": "Rates held steady.",
    }
    assert visible.metadata[1].url == "https://example.com/rates"
    assert search.await_count == 2
