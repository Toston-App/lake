"""Pins and structural checks for the recurring-detection cleanups.

The API JSON pins pass before and after the cleanups; the structural checks
describe the code shape the cleanups leave behind.
"""

import ast
import inspect
import random
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from app.ai import recurring_detection as ai
from app.core import recurrence, recurrence_detection
from app.core.recurrence_detection import AMOUNT_TOLERANCE
from app.models import User
from app.models.recurring_detection import RecurringDetectionUsage
from app.services import recurring_detection as detection
from app.services.transaction_kinds import KINDS
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from tests.services.test_recurring_detection import (
    TODAY,
    _isolate,  # noqa: F401  (autouse fixture)
    add_series,
    label_model,
    merchant_model,
)
from tests.utils import (
    create_test_account,
    create_test_expense,
    create_test_income,
    create_test_user,
)

PREFIX = "/api/v1/recurring"
APP_DIR = Path(detection.__file__).resolve().parents[1]
SERVICE_SOURCE = Path(detection.__file__).read_text()
SERVICE_TREE = ast.parse(SERVICE_SOURCE)


@pytest.fixture(autouse=True)
def _limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.rate_limits.hit_rate_limit", AsyncMock(return_value=(1, 3600))
    )


def _function(name: str) -> ast.AST:
    [node] = [
        n
        for n in ast.walk(SERVICE_TREE)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    ]
    return node


async def _stored_status(db: AsyncSession, user: User) -> str:
    return await db.scalar(
        text("SELECT status FROM recurring_detection_usage WHERE owner_id = :id"),
        {"id": user.id},
    )


# -- API JSON pins ----------------------------------------------------------------
async def test_api_json_for_status_detect_and_accept_is_pinned(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    await add_series(db_session, test_user)
    other = await create_test_user(db_session, email="pin-other@example.com")
    [theirs] = await add_series(db_session, other, count=1)

    status = (await client.get(f"{PREFIX}/detect/status")).json()
    assert status == {"running": False, "last_scan_at": None, "scans_remaining": 1}
    me = (await client.get("/api/v1/users/me")).json()
    assert me["recurring_detection"] == {"scans_remaining": 1}

    body = (await client.post(f"{PREFIX}/detect", json={"use_ai": False})).json()
    assert await _stored_status(db_session, test_user) == "done"
    assert set(body) == {
        "suggestions",
        "ai_used",
        "scanned_transactions",
        "scanned_at",
        "scans_remaining",
    }
    assert (body["ai_used"], body["scanned_transactions"]) == (False, 6)
    assert body["scans_remaining"] == 0
    [suggestion] = body["suggestions"]
    assert set(suggestion) == {
        "fingerprint",
        "type",
        "merchant_name",
        "description",
        "amount",
        "amount_min",
        "amount_max",
        "account_id",
        "to_account_id",
        "category_id",
        "subcategory_id",
        "place_id",
        "frequency",
        "interval",
        "weekdays",
        "monthly_mode",
        "month_days",
        "business_day_adjustment",
        "occurrences",
        "last_date",
        "next_expected_date",
        "confidence",
        "kind",
        "reasons",
    }
    assert suggestion["kind"] is None
    assert suggestion["type"] == "expense"

    status = (await client.get(f"{PREFIX}/detect/status")).json()
    assert status["running"] is False
    assert status["scans_remaining"] == 0
    assert status["last_scan_at"] is not None
    last = (await client.get(f"{PREFIX}/detect/last")).json()
    assert last["scans_remaining"] == 0
    me = (await client.get("/api/v1/users/me")).json()
    assert me["recurring_detection"] == {"scans_remaining": 0}

    item = {
        "fingerprint": suggestion["fingerprint"],
        "transaction_type": suggestion["type"],
        "transaction_id": suggestion["occurrences"][0]["transaction_id"],
        "frequency": suggestion["frequency"],
        "month_days": suggestion["month_days"],
    }
    response = await client.post(
        f"{PREFIX}/suggestions/accept",
        json={
            "items": [
                {**item, "fingerprint": "a" * 32, "transaction_id": theirs.id},
                item,
                item,
            ]
        },
    )
    assert response.status_code == 200
    missing, created, duplicate = response.json()
    assert missing == {
        "fingerprint": "a" * 32,
        "status": "error",
        "recurring": None,
        "error": "Transaction not found",
    }
    assert created["status"] == "created"
    assert created["error"] is None
    assert created["fingerprint"] == suggestion["fingerprint"]
    assert created["recurring"]["frequency"] == "monthly"
    assert duplicate == {
        "fingerprint": suggestion["fingerprint"],
        "status": "error",
        "recurring": None,
        "error": "This suggestion was already added",
    }

    # A free user who used their scan: 402 before the rate limit.
    limited = await client.post(f"{PREFIX}/detect")
    assert limited.status_code == 402
    assert limited.json()["detail"]["reason"] == "recurring_detection_limit"


async def test_paid_api_reports_unlimited_scans(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "tier1")
    await add_series(db_session, test_user)
    for _ in range(2):
        body = (await client.post(f"{PREFIX}/detect")).json()
        assert body["scans_remaining"] is None
    status = (await client.get(f"{PREFIX}/detect/status")).json()
    assert status["scans_remaining"] is None
    assert (await client.get(f"{PREFIX}/detect/last")).json()["scans_remaining"] is None
    me = (await client.get("/api/v1/users/me")).json()
    assert me["recurring_detection"] == {"scans_remaining": None}


async def test_unexpected_accept_error_json_is_pinned(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    await add_series(db_session, test_user)
    scan = await detection.detect(db_session, test_user, use_ai=False)
    [suggestion] = scan.result.suggestions

    async def boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr(detection.recurring_service, "create_from_transaction", boom)
    # The test session can't roll back without losing the fixtures; the
    # rollback itself isn't what's pinned here.
    monkeypatch.setattr(detection, "_rollback", AsyncMock())
    item = schemas.RecurringSuggestionAccept(
        fingerprint=suggestion.fingerprint,
        transaction_type=suggestion.type.value,
        transaction_id=suggestion.occurrences[0].transaction_id,
        frequency=suggestion.frequency,
        month_days=suggestion.month_days,
    )
    results = await detection.accept_suggestions(db_session, test_user, [item])
    assert [r.model_dump(mode="json") for r in results] == [
        {
            "fingerprint": suggestion.fingerprint,
            "status": "error",
            "recurring": None,
            "error": "Could not create this recurring transaction",
        }
    ]


async def test_stored_scan_statuses_are_plain_strings(
    db_session: AsyncSession, test_user: User
):
    claimed_at = await detection.claim_scan(db_session, test_user, unlimited=True)
    assert await _stored_status(db_session, test_user) == "running"
    await detection._finish_scan(
        db_session, test_user.id, claimed_at=claimed_at, status="cancelled"
    )
    assert await _stored_status(db_session, test_user) == "cancelled"
    usage = await db_session.get(RecurringDetectionUsage, test_user.id)
    usage.claimed_at -= detection.CANCEL_COOLDOWN * 2
    await db_session.commit()
    claimed_at = await detection.claim_scan(db_session, test_user, unlimited=True)
    await detection.release_scan(db_session, test_user.id, claimed_at)
    assert await _stored_status(db_session, test_user) == "done"


# -- schema fields ----------------------------------------------------------------
def test_accept_result_status_is_a_literal():
    for status in ("created", "error"):
        result = schemas.RecurringSuggestionAcceptResult(fingerprint="f", status=status)
        assert result.model_dump(mode="json")["status"] == status
    with pytest.raises(ValidationError):
        schemas.RecurringSuggestionAcceptResult(fingerprint="f", status="pending")
    prop = schemas.RecurringSuggestionAcceptResult.model_json_schema()["properties"]
    assert prop["status"] == {
        "enum": ["created", "error"],
        "title": "Status",
        "type": "string",
    }


def test_suggestion_kind_is_one_of_the_reported_kinds():
    reported = {
        ai._REPORTED_KIND.get(kind, kind)
        for kind in ai.CandidateLabel.model_fields["kind"].annotation.__args__
    }
    assert reported == {"subscription", "bill", "salary", "transfer", "other"}
    field = schemas.RecurringSuggestion.model_json_schema()["properties"]["kind"]
    [enum_branch] = [b for b in field["anyOf"] if "enum" in b]
    assert set(enum_branch["enum"]) == reported
    assert enum_branch["type"] == "string"
    assert {"type": "null"} in field["anyOf"]
    assert field["default"] is None

    base = {
        "fingerprint": "f",
        "type": "expense",
        "merchant_name": "M",
        "amount": 1,
        "amount_min": 1,
        "amount_max": 1,
        "frequency": "monthly",
        "interval": 1,
        "occurrences": [],
        "last_date": TODAY.isoformat(),
        "confidence": 0.9,
    }
    for kind in [*reported, None]:
        suggestion = schemas.RecurringSuggestion(**base, kind=kind)
        assert suggestion.model_dump(mode="json")["kind"] == kind
    with pytest.raises(ValidationError):
        schemas.RecurringSuggestion(**base, kind="habit")


async def test_ai_kind_reaches_the_suggestion_json(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", True)
    await add_series(db_session, test_user)
    with (
        ai.merchant_agent.override(model=merchant_model({}, [])),
        ai.classify_agent.override(model=label_model("subscription", True, 0.9)),
    ):
        scan = await detection.detect(db_session, test_user)
    [found] = scan.result.model_dump(mode="json")["suggestions"]
    assert found["kind"] == "subscription"


# -- structure --------------------------------------------------------------------
def test_scan_statuses_are_an_enum_with_the_stored_values():
    status = detection.ScanStatus
    assert issubclass(status, str)
    assert {s.value for s in status} == {"running", "done", "cancelled"}
    # The column stores the same strings: no migration.
    column = RecurringDetectionUsage.__table__.c.status
    assert column.default.arg == status.done.value
    assert column.server_default.arg == status.done.value

    stored = {"running", "done", "cancelled"}

    def literals(tree: ast.AST) -> list[str]:
        return [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value in stored
        ]

    [enum_class] = [
        n
        for n in ast.walk(SERVICE_TREE)
        if isinstance(n, ast.ClassDef) and n.name == "ScanStatus"
    ]
    # Spelled out only in the enum itself.
    assert sorted(literals(SERVICE_TREE)) == sorted(literals(enum_class))
    for name in ("RUNNING", "DONE", "CANCELLED"):
        assert not hasattr(detection, name)


def test_recurring_type_alias_is_gone():
    name = "Recurring" + "Type"
    assert not hasattr(recurrence, name)
    for path in [*APP_DIR.rglob("*.py"), *(APP_DIR.parent / "tests").rglob("*.py")]:
        assert name not in path.read_text().replace(name + "s", ""), path


def test_scans_remaining_has_one_source_of_truth():
    # Only `_remaining` works out the number. The one comparison on
    # `scan_count` left is the claim's SQL predicate (`Usage.scan_count`).
    compared = []
    for node in ast.walk(SERVICE_TREE):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Compare):
                for side in [sub.left, *sub.comparators]:
                    if isinstance(side, ast.Attribute) and side.attr == "scan_count":
                        compared.append((node.name, ast.unparse(side)))
    assert compared == [("claim_scan", "Usage.scan_count")]
    assert "FREE_SCANS - scan_count" in ast.unparse(_function("_remaining"))
    for name in ("scans_remaining", "scan_status", "last_result", "_check_limit"):
        assert "_remaining(" in ast.unparse(_function(name)), name
    for node in ast.walk(SERVICE_TREE):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Attribute) and t.attr == "scans_remaining"
            for t in node.targets
        ):
            assert isinstance(node.value, ast.Call), ast.unparse(node)
            assert ast.unparse(node.value.func) == "_remaining", ast.unparse(node)
    # check_access and claim_scan share the limit check.
    raisers = [
        n.name
        for n in ast.walk(SERVICE_TREE)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and "raise ScanLimitError(" in ast.unparse(n)
    ]
    assert raisers == ["_check_limit"]
    for name in ("check_access", "claim_scan"):
        assert "_check_limit(" in ast.unparse(_function(name))


def test_accept_errors_are_built_by_one_helper():
    source = ast.unparse(_function("accept_suggestions"))
    assert source.count("RecurringSuggestionAcceptResult(") == 1
    assert "'error'" not in source
    assert source.count("_failed(") == 3
    helper = ast.unparse(_function("_failed"))
    assert "status='error'" in helper


def test_drop_covered_reuses_the_amount_tolerance():
    source = ast.unparse(_function("_drop_covered"))
    assert "_within(" in source
    assert "median_amount <=" not in source and "AMOUNT_TOLERANCE *" not in source


def test_within_matches_the_old_no_text_tolerance():
    """`_drop_covered`'s old check, `abs(t - m) <= AMOUNT_TOLERANCE * m`, is
    `_within(t, m, AMOUNT_TOLERANCE, exact=False)` for every positive median
    (scans only load amounts > 0, so medians are positive)."""
    rng = random.Random(1234)
    cases = [(110.0, 100.0), (90.0, 100.0), (110.01, 100.0), (89.99, 100.0)]
    cases += [(1.1, 1.0), (0.9, 1.0), (16.5, 15.0), (13.5, 15.0), (0.01, 0.01)]
    for _ in range(20000):
        median = round(rng.uniform(0.01, 5000), rng.choice([0, 2]))
        amount = round(median * rng.uniform(0.85, 1.15), rng.choice([0, 2]))
        cases.append((amount, max(median, 0.01)))
    for amount, median in cases:
        old = abs(amount - median) <= AMOUNT_TOLERANCE * median
        new = recurrence_detection._within(amount, median, AMOUNT_TOLERANCE, False)
        assert old == new, (amount, median)


# -- deterministic loading --------------------------------------------------------
async def test_load_points_is_ordered_by_kind_date_and_id(
    db_session: AsyncSession, test_user: User
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    days = [40, 5, 90, 5, 60, 20]  # ids ascend in this (non-chronological) order
    for n, back in enumerate(days):
        await create_test_expense(
            db_session,
            owner_id=test_user.id,
            amount=10 + n,
            expense_date=TODAY - timedelta(days=back),
            description=f"shop {n}",
            account_id=account.id,
        )
        await create_test_income(
            db_session,
            owner_id=test_user.id,
            amount=100 + n,
            income_date=TODAY - timedelta(days=back),
            description=f"pay {n}",
            account_id=account.id,
        )

    points = await detection.load_points(db_session, test_user, TODAY)
    order = list(KINDS)
    assert points == sorted(points, key=lambda p: (order.index(p.type), p.date, p.id))
    assert [p.type for p in points] == sorted((p.type for p in points), key=order.index)
    assert len(points) == 2 * len(days)
    # Same date: the lower id first.
    same_day = [p for p in points if p.date == TODAY - timedelta(days=5)]
    assert [p.id for p in same_day if p.type == order[0]] == sorted(
        p.id for p in same_day if p.type == order[0]
    )


def test_load_points_query_orders_by_date_then_id():
    source = inspect.getsource(detection.load_points)
    assert "order_by(model.date, model.id)" in source
