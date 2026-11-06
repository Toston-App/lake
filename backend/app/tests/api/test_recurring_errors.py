"""Exact error responses of /api/v1/recurring.

Pins status, JSON body and headers for each service-error path, plus the
telemetry /detect records on them, so the routing of ServiceError through
the app-level exception handler can't change what clients see.
"""

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from app.core.config import settings
from app.models import RecurringTransaction, User
from app.services import recurring_detection as detection
from app.services.errors import ConflictError, DomainError, ServiceError, raise_http
from app.utilities import wide_events
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import create_test_account, create_test_expense, create_test_user

PREFIX = "/api/v1/recurring"
TODAY = datetime.now(timezone.utc).date()
RECURRING_ENDPOINTS = (
    Path(__file__).resolve().parents[2] / "app/api/api_v1/endpoints/recurring.py"
)


class FakeAxiomClient:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log(self, event: dict) -> None:
        self.events.append(json.loads(json.dumps(event, default=str)))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "free")
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )
    limiter = AsyncMock(return_value=(1, 3600))
    monkeypatch.setattr("app.services.rate_limits.hit_rate_limit", limiter)
    return limiter


@pytest.fixture
def axiom(monkeypatch: pytest.MonkeyPatch) -> FakeAxiomClient:
    fake = FakeAxiomClient()
    monkeypatch.setattr(wide_events, "get_axiom_client", lambda: fake)
    return fake


def _weekly_expense(**overrides) -> dict:
    payload = {
        "type": "expense",
        "amount": 10,
        "description": "Gym",
        "frequency": "weekly",
        "start_date": (TODAY - timedelta(days=14)).isoformat(),
        "timezone": "UTC",
    }
    payload.update(overrides)
    return payload


def _assert_error(response, status: int, detail, retry_after: str | None = None):
    assert response.status_code == status, response.text
    assert response.json() == {"detail": detail}
    assert response.headers.get("Retry-After") == retry_after
    assert response.headers["content-type"] == "application/json"


async def _foreign_template(db: AsyncSession) -> RecurringTransaction:
    other = await create_test_user(db, email="other@example.com")
    account = await create_test_account(db, owner_id=other.id)
    template = RecurringTransaction(
        owner_id=other.id,
        type="expense",
        amount=1,
        account_id=account.id,
        frequency="monthly",
        start_date=TODAY,
    )
    db.add(template)
    await db.commit()
    return template


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
async def test_create_domain_error(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    response = await client.post(
        PREFIX,
        json={
            "type": "transfer",
            "amount": 5,
            "frequency": "monthly",
            "start_date": TODAY.isoformat(),
            "account_id": account.id,
            "to_account_id": account.id,
        },
    )
    _assert_error(response, 400, "Transfers need two different accounts")


async def test_create_not_found(client: AsyncClient, db_session: AsyncSession):
    other = await create_test_user(db_session, email="other@example.com")
    account = await create_test_account(db_session, owner_id=other.id)
    response = await client.post(PREFIX, json=_weekly_expense(account_id=account.id))
    _assert_error(response, 404, "Account not found")


async def test_preview_domain_error(client: AsyncClient):
    response = await client.post(
        f"{PREFIX}/preview",
        json={
            "frequency": "weekly",
            "start_date": TODAY.isoformat(),
            "end_date": (TODAY - timedelta(days=1)).isoformat(),
        },
    )
    _assert_error(response, 400, "end_date must be on or after start_date")


async def test_from_transaction_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    other = await create_test_user(db_session, email="other@example.com")
    expense = await create_test_expense(db_session, owner_id=other.id)
    response = await client.post(
        f"{PREFIX}/from-transaction",
        json={
            "transaction_type": "expense",
            "transaction_id": expense.id,
            "frequency": "monthly",
        },
    )
    _assert_error(response, 404, "Transaction not found")


async def test_from_transaction_domain_error(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    expense = await create_test_expense(
        db_session,
        owner_id=test_user.id,
        account_id=account.id,
        expense_date=TODAY - timedelta(days=1),
    )
    payload = {
        "transaction_type": "expense",
        "transaction_id": expense.id,
        "frequency": "monthly",
        "timezone": "UTC",
    }
    first = await client.post(f"{PREFIX}/from-transaction", json=payload)
    assert first.status_code == 200, first.text
    again = await client.post(f"{PREFIX}/from-transaction", json=payload)
    _assert_error(again, 400, "This transaction already belongs to a recurring series")


async def test_by_id_not_found(client: AsyncClient, db_session: AsyncSession):
    foreign = await _foreign_template(db_session)
    url = f"{PREFIX}/{foreign.id}"
    missing = "Recurring transaction not found"
    _assert_error(await client.get(url), 404, missing)
    _assert_error(await client.put(url, json={"amount": 5}), 404, missing)
    _assert_error(await client.post(f"{url}/skip"), 404, missing)
    _assert_error(await client.delete(url), 404, missing)
    _assert_error(await client.get(f"{PREFIX}/999999"), 404, missing)


async def test_update_domain_error(client: AsyncClient):
    created = await client.post(PREFIX, json=_weekly_expense())
    assert created.status_code == 200, created.text
    url = f"{PREFIX}/{created.json()['id']}"
    response = await client.put(
        url, json={"end_date": (TODAY - timedelta(days=30)).isoformat()}
    )
    _assert_error(response, 400, "end_date must be on or after start_date")


async def test_conflict_while_generating(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from app.services import recurring as recurring_service

    created = await client.post(PREFIX, json=_weekly_expense())
    url = f"{PREFIX}/{created.json()['id']}"

    async def busy(*_args, **_kwargs):
        raise ConflictError("busy")

    monkeypatch.setattr(recurring_service, "skip_next", busy)
    _assert_error(await client.post(f"{url}/skip"), 409, "busy")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
async def test_detect_scan_limit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, axiom: FakeAxiomClient
):
    last = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    async def used(*_args, **_kwargs):
        raise detection.ScanLimitError(last)

    monkeypatch.setattr(detection, "check_access", used)
    response = await client.post(f"{PREFIX}/detect")
    _assert_error(
        response,
        402,
        {
            "reason": "recurring_detection_limit",
            "message": "Recurring detection is limited to one scan on the free plan",
            "tier": "free",
            "last_scan_at": "2026-01-02T03:04:05+00:00",
            "upgrade_url": "/account",
        },
    )
    [event] = axiom.events
    # The outcome is added to the operation the endpoint recorded.
    assert event["operation"] == {
        "type": "recurring_detect",
        "use_ai": True,
        "outcome": "free_scan_used",
    }
    assert event["http"]["status_code"] == 402
    assert "error" not in event


async def test_detect_scan_limit_without_last_scan(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    async def used(*_args, **_kwargs):
        raise detection.ScanLimitError(None)

    monkeypatch.setattr(detection, "check_access", used)
    response = await client.post(f"{PREFIX}/detect")
    assert response.status_code == 402
    assert response.json()["detail"]["last_scan_at"] is None


async def test_detect_cooldown(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, axiom: FakeAxiomClient
):
    async def cooling(*_args, **_kwargs):
        raise detection.ScanCooldownError(17)

    monkeypatch.setattr(detection, "run_scan", cooling)
    response = await client.post(f"{PREFIX}/detect")
    _assert_error(
        response,
        429,
        {"reason": "cancel_cooldown", "retry_after": 17},
        retry_after="17",
    )
    [event] = axiom.events
    assert event["operation"] == {
        "type": "recurring_detect",
        "use_ai": True,
        "outcome": "cancel_cooldown",
    }
    assert event["http"]["status_code"] == 429


async def test_detect_conflict(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    axiom: FakeAxiomClient,
):
    await detection.claim_scan(db_session, test_user, unlimited=False)
    response = await client.post(f"{PREFIX}/detect", json={"use_ai": True})
    _assert_error(response, 409, "A scan is already running")
    [event] = axiom.events
    # A plain service error records no outcome of its own.
    assert event["operation"] == {"type": "recurring_detect", "use_ai": True}


async def test_detect_last_not_found(client: AsyncClient):
    _assert_error(await client.get(f"{PREFIX}/detect/last"), 404, "No scan yet")


async def test_dismiss_cap(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(detection, "MAX_DISMISSALS_PER_USER", 0)
    response = await client.post(
        f"{PREFIX}/suggestions/dismiss", json={"items": [{"fingerprint": "c" * 32}]}
    )
    _assert_error(
        response,
        400,
        "You can dismiss at most 0 suggestions; undo some dismissals first",
    )


async def test_undo_dismissal_not_found(client: AsyncClient):
    response = await client.delete(f"{PREFIX}/suggestions/dismissals/{'d' * 32}")
    _assert_error(response, 404, "Dismissal not found")


# ---------------------------------------------------------------------------
# raise_http, still used by other routers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "detail", "headers"),
    [
        (DomainError("bad"), "bad", None),
        (
            detection.ScanCooldownError(5),
            {"reason": "cancel_cooldown", "retry_after": 5},
            {"Retry-After": "5"},
        ),
    ],
)
def test_raise_http(exc: ServiceError, detail, headers):
    with pytest.raises(HTTPException) as caught:
        raise_http(exc)
    assert caught.value.status_code == exc.status_code
    assert caught.value.detail == detail
    assert caught.value.headers == headers


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
def test_recurring_router_leaves_service_errors_to_the_app_handler():
    tree = ast.parse(RECURRING_ENDPOINTS.read_text())
    names = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    assert "raise_http" not in names
    assert "ServiceError" not in names
    caught = [
        ast.unparse(handler.type)
        for handler in ast.walk(tree)
        if isinstance(handler, ast.ExceptHandler) and handler.type is not None
    ]
    assert not [c for c in caught if "Error" in c and "Scan" in c], caught
    assert "except ServiceError" not in RECURRING_ENDPOINTS.read_text()


def test_service_error_handler_is_registered():
    from app.main import app

    assert ServiceError in app.exception_handlers
