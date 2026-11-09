"""Exact responses of the hourly/windowed rate limiters on recurring and chat.

The limiter is stubbed at the Redis call (``r.eval``), below every helper, so
these pin what clients see whichever layer enforces the limit.
"""

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from app.api import deps
from app.core.config import settings
from app.main import app
from app.models import User
from app.utilities import redis as redis_util
from app.utilities import wide_events
from httpx import AsyncClient

ENDPOINTS = Path(__file__).resolve().parents[2] / "app/api/api_v1/endpoints"
ACCEPT_BODY = {
    "items": [
        {
            "fingerprint": "a" * 32,
            "transaction_type": "expense",
            "transaction_id": 1,
            "frequency": "monthly",
        }
    ]
}


class FakeAxiomClient:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log(self, event: dict) -> None:
        self.events.append(json.loads(json.dumps(event, default=str)))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "free")
    monkeypatch.setattr(settings, "RECURRING_DETECT_RATE_LIMIT_PER_HOUR", 10)
    monkeypatch.setattr(settings, "RECURRING_ACCEPT_RATE_LIMIT_PER_HOUR", 60)
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_MINUTE", 10)
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_PER_DAY", 300)
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )


@pytest.fixture
def axiom(monkeypatch: pytest.MonkeyPatch) -> FakeAxiomClient:
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    # Keep every event, not a sample of the successful ones.
    monkeypatch.setattr(
        wide_events.WideEventsMiddleware, "_should_sample", lambda *_args: True
    )
    return fake


@pytest.fixture
def enriched(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Every enrich_event call /recurring makes."""
    from app.api.api_v1.endpoints import recurring as endpoint

    calls: list[dict] = []
    real = endpoint.enrich_event

    def spy(request, **kwargs):
        calls.append(kwargs)
        real(request, **kwargs)

    monkeypatch.setattr(endpoint, "enrich_event", spy)
    return calls


def _skips(enriched: list[dict]) -> list[dict]:
    return [c for c in enriched if "ai_skipped" in c.get("operation", {})]


@pytest.fixture
def detect_use_ai(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """The use_ai each detection pass actually ran with."""
    from app.services.recurring_detection import scan as detection_scan

    seen: list[bool] = []
    detect = detection_scan.detect

    async def spy(*args, **kwargs):
        seen.append(kwargs["use_ai"])
        return await detect(*args, **kwargs)

    monkeypatch.setattr(detection_scan, "detect", spy)
    return seen


@pytest.fixture
def redis_eval(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    stub = AsyncMock(return_value=1)
    monkeypatch.setattr(redis_util.r, "eval", stub)
    return stub


@pytest.fixture
def chat_client(client: AsyncClient, test_user: User) -> AsyncClient:
    app.dependency_overrides[deps.require_chat_access] = lambda: test_user
    return client


def _assert_rate_limited(response, window: int) -> None:
    assert response.status_code == 429, response.text
    body = response.json()
    assert set(body) == {"detail"}
    assert set(body["detail"]) == {"reason", "retry_after"}
    assert body["detail"]["reason"] == "rate_limited"
    retry_after = body["detail"]["retry_after"]
    assert isinstance(retry_after, int) and 0 < retry_after <= window
    assert response.headers["Retry-After"] == str(retry_after)


# ---------------------------------------------------------------------------
# Recurring
# ---------------------------------------------------------------------------
async def test_detect_rate_limited(
    client: AsyncClient,
    redis_eval: AsyncMock,
    axiom: FakeAxiomClient,
    enriched: list[dict],
):
    redis_eval.return_value = 11
    _assert_rate_limited(await client.post("/api/v1/recurring/detect"), 3600)
    [call] = redis_eval.await_args_list
    [key] = call.kwargs["keys"]
    assert key.startswith("rate-limit:recurring_detect:")
    assert call.kwargs["args"] == ["3600"]
    assert _skips(enriched) == []
    [event] = axiom.events
    assert event["http"]["status_code"] == 429
    assert event["operation"] == {"type": "recurring_detect", "use_ai": True}
    assert "error" not in event


async def test_detect_at_limit_is_allowed(client: AsyncClient, redis_eval: AsyncMock):
    redis_eval.return_value = 10
    response = await client.post("/api/v1/recurring/detect", json={"use_ai": False})
    assert response.status_code == 200, response.text


async def test_detect_limiter_down_scans_without_ai(
    client: AsyncClient,
    redis_eval: AsyncMock,
    axiom: FakeAxiomClient,
    enriched: list[dict],
    detect_use_ai: list[bool],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", True)
    redis_eval.side_effect = RuntimeError("redis down")
    response = await client.post("/api/v1/recurring/detect", json={"use_ai": True})
    assert response.status_code == 200, response.text
    assert response.json()["ai_used"] is False
    assert detect_use_ai == [False]
    assert _skips(enriched) == [{"operation": {"ai_skipped": "rate_limiter_down"}}]
    [event] = axiom.events
    assert event["http"]["status_code"] == 200
    assert event["operation"]["type"] == "recurring_detect"
    assert event["operation"]["ai_used"] is False
    # The skip recorded before the scan survives the operation added after it.
    assert event["operation"]["ai_skipped"] == "rate_limiter_down"


async def test_detect_limiter_down_without_ai_records_no_skip(
    client: AsyncClient,
    redis_eval: AsyncMock,
    enriched: list[dict],
    detect_use_ai: list[bool],
):
    redis_eval.side_effect = RuntimeError("redis down")
    response = await client.post("/api/v1/recurring/detect", json={"use_ai": False})
    assert response.status_code == 200, response.text
    assert detect_use_ai == [False]
    assert _skips(enriched) == []


async def test_detect_limit_disabled_skips_limiter(
    client: AsyncClient,
    redis_eval: AsyncMock,
    enriched: list[dict],
    detect_use_ai: list[bool],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "RECURRING_DETECT_RATE_LIMIT_PER_HOUR", 0)
    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", True)
    response = await client.post("/api/v1/recurring/detect", json={"use_ai": True})
    assert response.status_code == 200, response.text
    redis_eval.assert_not_awaited()
    # A disabled limit isn't a down limiter: the AI stays allowed.
    assert detect_use_ai == [True]
    assert _skips(enriched) == []


async def test_accept_rate_limited(client: AsyncClient, redis_eval: AsyncMock):
    redis_eval.return_value = 61
    response = await client.post(
        "/api/v1/recurring/suggestions/accept", json=ACCEPT_BODY
    )
    _assert_rate_limited(response, 3600)
    [key] = redis_eval.await_args.kwargs["keys"]
    assert key.startswith("rate-limit:recurring_accept:")


async def test_accept_limiter_down_fails_open(
    client: AsyncClient, redis_eval: AsyncMock
):
    redis_eval.side_effect = RuntimeError("redis down")
    response = await client.post(
        "/api/v1/recurring/suggestions/accept", json=ACCEPT_BODY
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
async def test_chat_rate_limited_per_minute(
    chat_client: AsyncClient, redis_eval: AsyncMock
):
    redis_eval.return_value = 11
    _assert_rate_limited(await chat_client.post("/api/v1/chat", json={}), 60)
    [call] = redis_eval.await_args_list
    assert call.kwargs["args"] == ["60"]
    [key] = call.kwargs["keys"]
    assert key.startswith("rate-limit:chat:")


async def test_chat_rate_limited_per_day(
    chat_client: AsyncClient, redis_eval: AsyncMock
):
    redis_eval.side_effect = [1, 301]
    _assert_rate_limited(await chat_client.post("/api/v1/chat", json={}), 86_400)
    assert [c.kwargs["args"] for c in redis_eval.await_args_list] == [
        ["60"],
        ["86400"],
    ]


async def test_chat_limiter_down_fails_closed(
    chat_client: AsyncClient, redis_eval: AsyncMock
):
    redis_eval.side_effect = RuntimeError("redis down")
    response = await chat_client.post("/api/v1/chat", json={})
    assert response.status_code == 503
    assert response.json() == {"detail": {"reason": "rate_limit_unavailable"}}
    assert "Retry-After" not in response.headers


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("module", ["recurring.py", "chat.py"])
def test_endpoints_use_shared_limiter(module: str):
    source = (ENDPOINTS / module).read_text()
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {
        alias.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        for alias in n.names
    }
    assert "hit_rate_limit" not in names
    # No hand-built 429s: the shared helper owns the response.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "status_code":
                    assert not (
                        isinstance(kw.value, ast.Constant) and kw.value.value == 429
                    ), f"{module}:{node.lineno} builds a 429"
    assert '"rate_limited"' not in source
    assert "Retry-After" not in source
