from unittest.mock import AsyncMock

import pytest
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
from fastapi import Request, Response

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
    path="/api/v1/investments/assets", *, query="", endpoint_name="list_assets"
):
    async def endpoint():
        return None

    endpoint.__name__ = endpoint_name
    return Request(
        {
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
    )


class FakeAxiomClient:
    def __init__(self):
        self.events = []

    async def log(self, event):
        self.events.append(event)


def make_middleware(sample_rate=0.0):
    return WideEventsMiddleware(
        AsyncMock(), service_name="test", service_version="1", sample_rate=sample_rate
    )


def test_operation_inventory_covers_all_routes():
    assert set(_RESOURCE_BY_OPERATION) == EXPECTED_OPERATIONS
    assert len(_RESOURCE_BY_OPERATION) == 28


def test_helpers_merge_context_stages_results_and_redact_values():
    request = make_request(endpoint_name="create_transaction_with_asset")
    request.state.wide_event = {"request_id": "request-1"}
    start_investment_event(request, user_id=7)
    add_investment_context(request, account_id=11, provider="yahoo", price=9999)
    with investment_stage(request, "asset_resolution"):
        pass
    add_investment_context(request, asset_id=13, holding_id=17)
    complete_investment_event(
        request, asset_created=True, holding_created=False, total_value=9999
    )
    investment = request.state.wide_event["investment"]
    assert investment["operation"] == "create_transaction_with_asset"
    assert investment["resource"] == "transactions"
    assert investment["completed_stages"] == ["asset_resolution"]
    assert investment["outcome"] == "success"
    assert investment["result"] == {"asset_created": True, "holding_created": False}
    assert "9999" not in str(investment)


def test_failure_and_partial_failure_are_normalized():
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
        request, reason="prices_unavailable", failed_count=2, updated_count=4
    )
    assert request.state.wide_event["force_log"] is True
    assert request.state.wide_event["investment"]["result"] == {
        "failed_count": 2,
        "updated_count": 4,
    }


def test_http_context_redacts_search_and_referer_query():
    request = make_request(
        query="q=secret+company&limit=20&account_id=4&unexpected=private"
    )
    request.scope["headers"] = [
        (b"referer", b"https://dashboard.test/investments?q=secret#private")
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
async def test_failures_are_always_emitted(monkeypatch):
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    response = await make_middleware().dispatch(
        make_request(endpoint_name="get_asset"),
        AsyncMock(return_value=Response(status_code=404)),
    )
    assert response.status_code == 404
    assert len(fake.events) == 1
    assert fake.events[0]["sampling_reason"] == "error"
    assert fake.events[0]["investment"]["failure"]["reason"] == "http_404"


@pytest.mark.asyncio
async def test_successes_remain_sampled(monkeypatch):
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    await make_middleware().dispatch(
        make_request(), AsyncMock(return_value=Response(status_code=200))
    )
    assert fake.events == []


@pytest.mark.asyncio
async def test_partial_success_bypasses_sampling(monkeypatch):
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    request = make_request(endpoint_name="refresh_all_prices")

    async def call_next(_):
        partial_investment_failure(
            request, reason="prices_unavailable", failed_count=1, updated_count=2
        )
        return Response(status_code=200)

    await make_middleware().dispatch(request, call_next)
    assert len(fake.events) == 1
    assert fake.events[0]["investment"]["outcome"] == "partial_failure"


@pytest.mark.asyncio
async def test_unexpected_error_omits_sensitive_message(monkeypatch):
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    secret = "price=9999 notes=private email=user@example.com"

    async def call_next(_):
        raise RuntimeError(secret)

    with pytest.raises(RuntimeError):
        await make_middleware().dispatch(make_request(), call_next)
    assert fake.events[0]["error"]["type"] == "RuntimeError"
    assert "message" not in fake.events[0]["error"]
    assert secret not in str(fake.events[0])


@pytest.mark.asyncio
async def test_axiom_failure_does_not_break_request(monkeypatch):
    failing = AsyncMock()
    failing.log.side_effect = RuntimeError("telemetry unavailable")
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: failing)
    response = await make_middleware(1.0).dispatch(
        make_request(), AsyncMock(return_value=Response(status_code=200))
    )
    assert response.status_code == 200
    failing.log.assert_awaited_once()
