from unittest.mock import AsyncMock

import pytest
from fastapi import Request, Response

from app.utilities import wide_events
from app.utilities.investment_telemetry import (
    _RESOURCE_BY_OPERATION,
    add_investment_context,
    complete_investment_event,
    fail_investment_event,
    investment_stage,
    partial_investment_failure,
    start_investment_event,
)
from app.utilities.wide_events import WideEventsMiddleware, _http_context

EXPECTED_OPERATIONS = {
    "list_assets",
    "create_asset",
    "search_assets",
    "search_external_assets",
    "search_crypto_assets",
    "get_asset",
    "update_asset",
    "delete_asset",
    "get_asset_price",
    "refresh_all_prices",
    "list_holdings",
    "create_holding",
    "get_holding",
    "update_holding",
    "delete_holding",
    "list_transactions",
    "create_transaction",
    "create_transaction_with_asset",
    "get_transaction",
    "delete_transaction",
    "get_portfolio_summary",
    "get_allocation_by_class",
    "get_allocation_by_currency",
    "get_allocation_by_market",
    "get_allocation_by_type",
    "get_allocation_by_country",
    "get_allocation_by_account",
    "get_top_holdings",
}


def make_request(
    path: str = "/api/v1/investments/assets",
    *,
    query: str = "",
    endpoint_name: str = "list_assets",
) -> Request:
    async def endpoint() -> None:
        return None

    endpoint.__name__ = endpoint_name
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
        "endpoint": endpoint,
    }
    return Request(scope)


class FakeAxiomClient:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log(self, event: dict) -> None:
        self.events.append(event)


def make_middleware(*, sample_rate: float = 0.0) -> WideEventsMiddleware:
    return WideEventsMiddleware(
        AsyncMock(),
        service_name="test",
        service_version="1",
        sample_rate=sample_rate,
    )


def test_operation_inventory_covers_all_investment_routes() -> None:
    assert set(_RESOURCE_BY_OPERATION) == EXPECTED_OPERATIONS
    assert len(_RESOURCE_BY_OPERATION) == 28


def test_helpers_merge_context_stages_and_results() -> None:
    request = make_request(endpoint_name="create_transaction_with_asset")
    request.state.wide_event = {"request_id": "request-1"}

    start_investment_event(request, user_id=7)
    add_investment_context(request, account_id=11, provider="yahoo", price=9999)
    with investment_stage(request, "asset_resolution"):
        pass
    add_investment_context(request, asset_id=13, holding_id=17)
    complete_investment_event(
        request,
        asset_created=True,
        holding_created=False,
        total_value=9999,
    )

    investment = request.state.wide_event["investment"]
    assert investment["operation"] == "create_transaction_with_asset"
    assert investment["resource"] == "transactions"
    assert investment["user_id"] == 7
    assert investment["account_id"] == 11
    assert investment["asset_id"] == 13
    assert investment["holding_id"] == 17
    assert "price" not in investment
    assert investment["completed_stages"] == ["asset_resolution"]
    assert investment["timings"]["asset_resolution"] >= 0
    assert investment["outcome"] == "success"
    assert investment["result"] == {
        "asset_created": True,
        "holding_created": False,
    }


def test_failure_and_partial_failure_are_normalized() -> None:
    request = make_request(endpoint_name="refresh_all_prices")
    request.state.wide_event = {}
    start_investment_event(request, user_id=3)
    fail_investment_event(request, reason="rate_limited", stage="rate_limit")
    assert request.state.wide_event["investment"]["failure"] == {
        "kind": "expected",
        "reason": "rate_limited",
        "stage": "rate_limit",
    }

    partial_investment_failure(
        request,
        reason="prices_unavailable",
        failed_count=2,
        updated_count=4,
    )
    assert request.state.wide_event["force_log"] is True
    assert request.state.wide_event["investment"]["outcome"] == "partial_failure"
    assert request.state.wide_event["investment"]["result"] == {
        "failed_count": 2,
        "updated_count": 4,
    }


def test_helpers_are_noops_without_middleware_state() -> None:
    request = make_request()
    assert start_investment_event(request) is None
    add_investment_context(request, account_id=1)
    complete_investment_event(request, result_count=1)
    fail_investment_event(request, reason="not_found")
    partial_investment_failure(request, reason="partial", failed_count=1)


def test_investment_http_context_redacts_raw_search_and_url() -> None:
    request = make_request(
        query="q=secret+company&limit=20&account_id=4&unexpected=private"
    )
    request.scope["headers"] = [
        (b"referer", b"https://dashboard.test/investments?q=referer-secret#private")
    ]
    context = _http_context(request)

    assert context["url"] == "https://test/api/v1/investments/assets"
    assert context["query_params"] == {
        "limit": "20",
        "account_id": "4",
        "search_query_present": True,
        "search_query_length": 14,
    }
    assert "secret" not in str(context)
    assert "unexpected" not in str(context)
    assert context["referer"] == "https://dashboard.test/investments"


@pytest.mark.asyncio
async def test_http_failures_are_classified_and_always_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    middleware = make_middleware(sample_rate=0.0)
    request = make_request(endpoint_name="get_asset")

    response = await middleware.dispatch(
        request,
        AsyncMock(return_value=Response(status_code=404)),
    )

    assert response.status_code == 404
    assert len(fake.events) == 1
    event = fake.events[0]
    assert event["outcome"] == "error"
    assert event["sampling_reason"] == "error"
    assert event["investment"]["outcome"] == "failure"
    assert event["investment"]["failure"]["reason"] == "http_404"


@pytest.mark.asyncio
async def test_successes_remain_sampled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    middleware = make_middleware(sample_rate=0.0)

    await middleware.dispatch(
        make_request(),
        AsyncMock(return_value=Response(status_code=200)),
    )

    assert fake.events == []


@pytest.mark.asyncio
async def test_partial_success_bypasses_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    middleware = make_middleware(sample_rate=0.0)
    request = make_request(endpoint_name="refresh_all_prices")

    async def call_next(_: Request) -> Response:
        partial_investment_failure(
            request,
            reason="prices_unavailable",
            failed_count=1,
            updated_count=2,
        )
        return Response(status_code=200)

    await middleware.dispatch(request, call_next)

    assert len(fake.events) == 1
    assert fake.events[0]["sampling_reason"] == "debug_mode"
    assert fake.events[0]["investment"]["outcome"] == "partial_failure"


@pytest.mark.asyncio
async def test_unexpected_investment_error_omits_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    middleware = make_middleware(sample_rate=0.0)
    sensitive_value = "price=9999 notes=private"

    async def call_next(_: Request) -> Response:
        raise RuntimeError(sensitive_value)

    with pytest.raises(RuntimeError):
        await middleware.dispatch(make_request(), call_next)

    assert len(fake.events) == 1
    error = fake.events[0]["error"]
    assert error["type"] == "RuntimeError"
    assert "message" not in error
    assert sensitive_value not in str(fake.events[0])
    assert fake.events[0]["investment"]["failure"]["kind"] == "unexpected"


@pytest.mark.asyncio
async def test_axiom_failure_does_not_break_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing_client = AsyncMock()
    failing_client.log.side_effect = RuntimeError("telemetry unavailable")
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: failing_client)
    middleware = make_middleware(sample_rate=1.0)

    response = await middleware.dispatch(
        make_request(),
        AsyncMock(return_value=Response(status_code=200)),
    )

    assert response.status_code == 200
    failing_client.log.assert_awaited_once()
