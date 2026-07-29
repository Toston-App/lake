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


async def test_agent_search_transactions_returns_only_requested_limit(
    monkeypatch,
) -> None:
    search = AsyncMock(
        return_value=SimpleNamespace(items=list(range(10)), total=10)
    )
    monkeypatch.setattr(agent.transaction_service, "search_transactions", search)
    ctx = SimpleNamespace(
        deps=SimpleNamespace(db=SimpleNamespace(), user=SimpleNamespace(id=1))
    )

    result = await agent.search_transactions(ctx, SearchTransactionsArgs(limit=5))

    assert result.metadata[0].data == {
        "items": [0, 1, 2, 3, 4],
        "total": 10,
        "truncated": True,
    }
    assert search.await_args.kwargs["params"].size == 5
