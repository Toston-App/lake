from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.ai import agent, chat_tools
from app.ai.tools import SearchTransactionsArgs
from pydantic import ValidationError


def test_search_transactions_limit_defaults_to_search_cap() -> None:
    assert SearchTransactionsArgs().limit == chat_tools.SEARCH_LIMIT


def test_search_transactions_limit_is_bounded() -> None:
    with pytest.raises(ValidationError):
        SearchTransactionsArgs(limit=0)

    with pytest.raises(ValidationError):
        SearchTransactionsArgs(limit=chat_tools.SEARCH_LIMIT + 1)


async def test_search_transactions_returns_only_requested_limit(monkeypatch) -> None:
    search = AsyncMock(
        return_value=SimpleNamespace(items=list(range(10)), total=10)
    )
    monkeypatch.setattr(chat_tools.transaction_service, "search_transactions", search)

    result, action_type = await chat_tools.execute_tool(
        "search_transactions",
        {"limit": 5},
        db=SimpleNamespace(),
        user=SimpleNamespace(id=1),
    )

    assert action_type == "transactions_searched"
    assert result["data"] == {
        "items": [0, 1, 2, 3, 4],
        "total": 10,
        "truncated": True,
    }
    assert search.await_args.kwargs["params"].size == 5


async def test_agent_search_exposes_compact_transaction_references_to_model(
    monkeypatch,
) -> None:
    items = [
        {
            "id": 12456,
            "type": "expense",
            "amount": 100.0,
            "date": "2026-07-28",
            "description": "Drinks",
            "owner_id": 389,
            "created_at": "2026-07-29T05:30:35+00:00",
            "category": {
                "name": "Food and drinks",
                "total": 100.0,
                "subcategories": [{"id": 1, "name": "Alcohol"}],
            },
            "subcategory": {"name": "Alcohol", "total": 100.0},
            "place": {"name": "Market"},
            "account": {"name": "Checking"},
        },
        {
            "id": 12457,
            "type": "income",
            "amount": 500.0,
            "date": "2026-07-27",
            "description": "Refund",
            "owner_id": 389,
            "created_at": "2026-07-28T05:30:35+00:00",
            "category": None,
            "subcategory": None,
            "place": None,
            "account": {"name": "Savings"},
        },
        {
            "id": 12458,
            "type": "transfer",
            "amount": 50.0,
            "date": "2026-07-26",
            "description": None,
            "owner_id": 389,
            "created_at": "2026-07-27T05:30:35+00:00",
        },
    ]
    search = AsyncMock(
        return_value=SimpleNamespace(items=items, total=10)
    )
    monkeypatch.setattr(agent.transaction_service, "search_transactions", search)
    ctx = SimpleNamespace(
        deps=SimpleNamespace(db=SimpleNamespace(), user=SimpleNamespace(id=1))
    )

    result = await agent.search_transactions(ctx, SearchTransactionsArgs(limit=5))

    assert result.metadata[0].data == {
        "items": items,
        "total": 10,
        "truncated": True,
    }
    assert result.return_value == {
        "summary": "Found 10 transactions.",
        "items": [
            {
                "id": 12456,
                "type": "expense",
                "amount": 100.0,
                "date": "2026-07-28",
                "description": "Drinks",
                "account_name": "Checking",
                "category_name": "Food and drinks",
                "subcategory_name": "Alcohol",
                "place_name": "Market",
            },
            {
                "id": 12457,
                "type": "income",
                "amount": 500.0,
                "date": "2026-07-27",
                "description": "Refund",
                "account_name": "Savings",
            },
            {
                "id": 12458,
                "type": "transfer",
                "amount": 50.0,
                "date": "2026-07-26",
                "description": None,
            },
        ],
        "total": 10,
        "truncated": True,
    }
    model_items = result.return_value["items"]
    assert [(item["id"], item["type"]) for item in model_items] == [
        (12456, "expense"),
        (12457, "income"),
        (12458, "transfer"),
    ]
    assert all("owner_id" not in item for item in model_items)
    assert all("created_at" not in item for item in model_items)
    assert all("total" not in item for item in model_items)
    assert all("subcategories" not in item for item in model_items)
    assert search.await_args.kwargs["params"].size == 5


async def test_agent_search_exposes_empty_model_reference_list(monkeypatch) -> None:
    search = AsyncMock(return_value=SimpleNamespace(items=[], total=0))
    monkeypatch.setattr(agent.transaction_service, "search_transactions", search)
    ctx = SimpleNamespace(
        deps=SimpleNamespace(db=SimpleNamespace(), user=SimpleNamespace(id=1))
    )

    result = await agent.search_transactions(ctx, SearchTransactionsArgs())

    assert result.return_value == {
        "summary": "Found 0 transactions.",
        "items": [],
        "total": 0,
        "truncated": False,
    }
    assert result.metadata[0].data == {
        "items": [],
        "total": 0,
        "truncated": False,
    }
