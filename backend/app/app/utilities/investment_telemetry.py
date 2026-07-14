"""Structured Axiom enrichment for investment request workflows.

The helpers in this module only mutate the request's in-memory wide event. The
wide-events middleware remains responsible for sampling and emitting exactly
one event per request.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any

from fastapi import Request

from app.utilities.wide_events import mark_for_logging

INVESTMENTS_PATH_PREFIX = "/api/v1/investments"

_RESOURCE_BY_OPERATION = {
    "list_assets": "assets",
    "create_asset": "assets",
    "search_assets": "assets",
    "search_external_assets": "assets",
    "search_crypto_assets": "assets",
    "get_asset": "assets",
    "update_asset": "assets",
    "delete_asset": "assets",
    "get_asset_price": "prices",
    "refresh_all_prices": "prices",
    "list_holdings": "holdings",
    "create_holding": "holdings",
    "get_holding": "holdings",
    "update_holding": "holdings",
    "delete_holding": "holdings",
    "list_transactions": "transactions",
    "create_transaction": "transactions",
    "create_transaction_with_asset": "transactions",
    "get_transaction": "transactions",
    "delete_transaction": "transactions",
    "get_portfolio_summary": "portfolio",
    "get_allocation_by_class": "portfolio",
    "get_allocation_by_currency": "portfolio",
    "get_allocation_by_market": "portfolio",
    "get_allocation_by_type": "portfolio",
    "get_allocation_by_country": "portfolio",
    "get_allocation_by_account": "portfolio",
    "get_top_holdings": "portfolio",
}

_CONTEXT_FIELDS = {
    "user_id",
    "account_id",
    "asset_id",
    "holding_id",
    "transaction_id",
    "symbol",
    "provider",
    "transaction_type",
    "asset_class",
    "asset_type",
    "currency",
    "market",
}

_RESULT_FIELDS = {
    "result_count",
    "updated_count",
    "failed_count",
    "holdings_count",
    "assets_count",
    "groups_count",
    "asset_created",
    "holding_created",
    "price_available",
    "refreshed",
    "cache_hit",
}


def is_investment_request(request: Request) -> bool:
    return request.url.path.startswith(INVESTMENTS_PATH_PREFIX)


def _wide_event(request: Request) -> dict[str, Any] | None:
    event = getattr(request.state, "wide_event", None)
    return event if isinstance(event, dict) else None


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _operation_name(request: Request) -> str:
    endpoint = request.scope.get("endpoint")
    name = getattr(endpoint, "__name__", None)
    if isinstance(name, str) and name:
        return name
    route = request.scope.get("route")
    route_name = getattr(route, "name", None)
    return route_name if isinstance(route_name, str) and route_name else "unknown"


def start_investment_event(
    request: Request,
    *,
    user_id: int | None = None,
    operation: str | None = None,
    resource: str | None = None,
) -> dict[str, Any] | None:
    """Initialize the stable investment event contract without replacing it."""
    event = _wide_event(request)
    if event is None:
        return None

    operation = operation or _operation_name(request)
    investment = event.setdefault("investment", {})
    investment.setdefault("operation", operation)
    investment.setdefault("resource", resource or _RESOURCE_BY_OPERATION.get(operation, "unknown"))
    investment.setdefault("outcome", "in_progress")
    investment.setdefault("current_stage", "access")
    investment.setdefault("completed_stages", [])
    investment.setdefault("timings", {})
    if user_id is not None:
        investment["user_id"] = user_id
    return investment


def add_investment_context(request: Request, **values: Any) -> None:
    """Merge approved identifiers and categorical context into the event."""
    investment = start_investment_event(request)
    if investment is None:
        return
    for key, value in values.items():
        if key in _CONTEXT_FIELDS and value is not None:
            investment[key] = _plain(value)


def begin_investment_stage(request: Request, stage: str) -> None:
    investment = start_investment_event(request)
    if investment is not None:
        investment["current_stage"] = stage


def complete_investment_stage(
    request: Request,
    stage: str,
    *,
    duration_ms: float | None = None,
) -> None:
    investment = start_investment_event(request)
    if investment is None:
        return
    completed = investment.setdefault("completed_stages", [])
    if stage not in completed:
        completed.append(stage)
    investment["current_stage"] = stage
    if duration_ms is not None:
        investment.setdefault("timings", {})[stage] = round(duration_ms, 2)


@contextmanager
def investment_stage(request: Request, stage: str) -> Iterator[None]:
    """Time a workflow stage while leaving failures for middleware finalization."""
    begin_investment_stage(request, stage)
    started_at = time.perf_counter()
    try:
        yield
    except Exception:
        investment = start_investment_event(request)
        if investment is not None:
            investment.setdefault("timings", {})[stage] = round(
                (time.perf_counter() - started_at) * 1000,
                2,
            )
        raise
    else:
        complete_investment_stage(
            request,
            stage,
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )


def fail_investment_event(
    request: Request,
    *,
    reason: str,
    kind: str = "expected",
    stage: str | None = None,
) -> None:
    investment = start_investment_event(request)
    if investment is None:
        return
    failed_stage = stage or investment.get("current_stage", "unknown")
    investment["outcome"] = "failure"
    investment["failure"] = {
        "kind": kind,
        "reason": reason,
        "stage": failed_stage,
    }


def complete_investment_event(request: Request, **result: Any) -> None:
    investment = start_investment_event(request)
    if investment is None:
        return
    investment["outcome"] = "success"
    if result:
        investment.setdefault("result", {}).update(
            {
                key: _plain(value)
                for key, value in result.items()
                if key in _RESULT_FIELDS and value is not None
            }
        )


def partial_investment_failure(
    request: Request,
    *,
    reason: str,
    failed_count: int,
    **result: Any,
) -> None:
    """Record and retain a logical failure that is represented by HTTP 2xx."""
    investment = start_investment_event(request)
    if investment is None:
        return
    investment["outcome"] = "partial_failure"
    investment["failure"] = {
        "kind": "partial",
        "reason": reason,
        "stage": investment.get("current_stage", "unknown"),
    }
    investment.setdefault("result", {}).update(
        {
            "failed_count": failed_count,
            **{
                key: _plain(value)
                for key, value in result.items()
                if key in _RESULT_FIELDS and value is not None
            },
        }
    )
    mark_for_logging(request)


def finalize_investment_response(request: Request, status_code: int) -> None:
    """Fill outcome/failure defaults for paths not explicitly completed by handlers."""
    if not is_investment_request(request):
        return
    investment = start_investment_event(request)
    if investment is None:
        return
    if status_code >= 400:
        if investment.get("outcome") != "failure":
            fail_investment_event(
                request,
                reason=f"http_{status_code}",
                kind="expected" if status_code < 500 else "unexpected",
            )
    elif investment.get("outcome") == "in_progress":
        complete_investment_event(request)
