"""The recurring and compare_periods tools against a real database.

Previews and execution go through the shared dispatcher
(``dispatch.execute_tool``) and preview builder, which both the in-app chat
and the MCP server use.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.ai import recurring_tools
from app.ai.confirmation import (
    build_pending_preview,
    format_confirmation_message,
    preview_is_current,
)
from app.ai.dispatch import execute_tool
from app.models import Expense, RecurringTransaction, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_expense,
    create_test_user,
)

TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )


def _weekly(account_id: int, **overrides) -> dict:
    return {
        "type": "expense",
        "amount": 10,
        "description": "Gym",
        "frequency": "weekly",
        "start_date": (TODAY - timedelta(days=14)).isoformat(),
        "timezone": "UTC",
        "account_id": account_id,
        "response_language": "en",
        **overrides,
    }


async def _expenses(db: AsyncSession, recurring_id: int) -> list[Expense]:
    result = await db.execute(
        select(Expense)
        .where(Expense.recurring_id == recurring_id)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def _create(db: AsyncSession, user: User, **overrides) -> dict:
    account = await create_test_account(db, owner_id=user.id, name="Main")
    result, action = await execute_tool(
        "create_recurring", _weekly(account.id, **overrides), db, user
    )
    assert result["success"], result
    assert action == "recurring_created"
    return result["data"]


async def test_create_preview_counts_back_filled_occurrences(
    db_session: AsyncSession, test_user: User
) -> None:
    account = await create_test_account(db_session, owner_id=test_user.id, name="Main")
    args = _weekly(account.id)

    preview = await build_pending_preview(
        db_session, test_user, "create_recurring", args
    )

    # Weekly from two weeks ago: today minus 14, minus 7 and today are due.
    assert preview["created_now"] == 3
    assert preview["created_now_is_minimum"] is False
    assert preview["labels"]["account_id"] == "Main"
    assert len(preview["upcoming"]) == 5
    message = format_confirmation_message("create_recurring", preview)
    assert "**Gym**" in message and "3 occurrences already due" in message


async def test_create_runs_and_back_fills(
    db_session: AsyncSession, test_user: User
) -> None:
    created = await _create(db_session, test_user)

    assert created["occurrences_count"] == 3
    assert len(await _expenses(db_session, created["id"])) == 3


async def test_list_filters_paused_series(
    db_session: AsyncSession, test_user: User
) -> None:
    created = await _create(db_session, test_user)
    result, _ = await execute_tool(
        "update_recurring",
        {"id": created["id"], "data": {"is_active": False}, "response_language": "en"},
        db_session,
        test_user,
    )
    assert result["success"] and result["data"]["is_active"] is False

    everything, _ = await execute_tool("list_recurring", {}, db_session, test_user)
    active, _ = await execute_tool(
        "list_recurring", {"active_only": True}, db_session, test_user
    )

    assert [s["id"] for s in everything["data"]] == [created["id"]]
    assert active["data"] == []


async def test_update_preview_shows_existing_and_changes(
    db_session: AsyncSession, test_user: User
) -> None:
    created = await _create(db_session, test_user)

    preview = await build_pending_preview(
        db_session,
        test_user,
        "update_recurring",
        {"id": created["id"], "data": {"amount": 12}},
    )

    assert preview["operation"] == "update"
    assert preview["existing"]["amount"] == 10
    assert preview["changes"] == {"amount": 12}
    # The chat card lists these, so the user sees the change before approving.
    assert preview["arguments"] == {"amount": 12}
    assert "Only future occurrences change" in format_confirmation_message(
        "update_recurring", preview
    )


async def test_skip_preview_names_the_skipped_date_then_skips(
    db_session: AsyncSession, test_user: User
) -> None:
    created = await _create(db_session, test_user)
    next_date = created["next_run_date"]

    preview = await build_pending_preview(
        db_session, test_user, "skip_recurring_occurrence", {"id": created["id"]}
    )
    assert preview["skipped_date"] == next_date

    result, _ = await execute_tool(
        "skip_recurring_occurrence",
        {"id": created["id"], "response_language": "en"},
        db_session,
        test_user,
    )
    assert result["data"]["next_run_date"] > next_date


async def test_delete_with_generated_transactions(
    db_session: AsyncSession, test_user: User
) -> None:
    created = await _create(db_session, test_user)
    args = {"id": created["id"], "delete_generated": True}

    preview = await build_pending_preview(
        db_session, test_user, "delete_recurring", args
    )
    assert preview["operation"] == "delete"
    assert "transactions it created" in format_confirmation_message(
        "delete_recurring", preview
    )

    result, _ = await execute_tool(
        "delete_recurring", {**args, "response_language": "en"}, db_session, test_user
    )
    assert result["success"]
    assert await db_session.get(RecurringTransaction, created["id"]) is None
    assert await _expenses(db_session, created["id"]) == []


async def test_from_transaction_preview_and_create(
    db_session: AsyncSession, test_user: User
) -> None:
    expense = await create_test_expense(
        db_session,
        owner_id=test_user.id,
        amount=15.99,
        description="Netflix",
        expense_date=TODAY - timedelta(days=3),
    )
    args = {
        "transaction_type": "expense",
        "transaction_id": expense.id,
        "frequency": "monthly",
    }

    preview = await build_pending_preview(
        db_session, test_user, "create_recurring_from_transaction", args
    )
    assert preview["display_name"] == "Netflix"
    assert preview["fields"]["amount"] == 15.99
    assert preview["source_transaction"]["id"] == expense.id
    # The transaction is the first occurrence; the next one is a month later.
    assert preview["created_now"] == 0

    result, _ = await execute_tool(
        "create_recurring_from_transaction",
        {**args, "response_language": "en"},
        db_session,
        test_user,
    )
    assert result["success"], result
    assert result["data"]["amount"] == 15.99


async def test_other_users_series_is_not_found(
    db_session: AsyncSession, test_user: User
) -> None:
    other = await create_test_user(db_session, email="other@example.com")
    created = await _create(db_session, other)

    result, _ = await execute_tool(
        "delete_recurring",
        {"id": created["id"], "response_language": "en"},
        db_session,
        test_user,
    )

    assert result["success"] is False
    assert "not found" in result["error"].lower()
    assert await db_session.get(RecurringTransaction, created["id"]) is not None


async def test_detect_slims_the_result(
    db_session: AsyncSession, test_user: User, monkeypatch
) -> None:
    suggestion = {"fingerprint": "f", "merchant_name": "Gym"}
    scan_result = SimpleNamespace(
        result={
            "suggestions": [suggestion] * 30,
            "ai_used": False,
            "scanned_transactions": 90,
        }
    )
    run_scan = AsyncMock(return_value=scan_result)
    monkeypatch.setattr(
        recurring_tools.detection_service, "check_access", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(recurring_tools.detection_service, "run_scan", run_scan)
    monkeypatch.setattr(
        recurring_tools, "enforce_rate_limit", AsyncMock(return_value=True)
    )

    result, _ = await execute_tool(
        "detect_recurring", {"response_language": "en"}, db_session, test_user
    )

    assert result["success"]
    assert len(result["data"]["suggestions"]) == 25
    assert result["data"]["suggestions_truncated"] is True
    assert run_scan.call_args.kwargs["use_ai"] is False


# -- compare_periods ---------------------------------------------------------------


async def _seed_two_months(db: AsyncSession, user: User) -> dict:
    food = await create_test_category(db, owner_id=user.id, name="Food")
    rent = await create_test_category(db, owner_id=user.id, name="Rent")
    for amount, day, category in (
        (100, date(2026, 9, 5), food),
        (500, date(2026, 9, 1), rent),
        (250, date(2026, 10, 7), food),
        (500, date(2026, 10, 1), rent),
    ):
        await create_test_expense(
            db,
            owner_id=user.id,
            amount=amount,
            expense_date=day,
            category_id=category.id,
        )
    return {"food": food, "rent": rent}


async def test_compare_with_previous_period(
    db_session: AsyncSession, test_user: User
) -> None:
    categories = await _seed_two_months(db_session, test_user)

    result, action = await execute_tool(
        "compare_periods",
        {"date_filter_type": "month", "date": "2026-10"},
        db_session,
        test_user,
    )

    assert action == "periods_compared"
    data = result["data"]
    assert data["period"]["start_date"] == "2026-10-01"
    assert data["compared_to"]["start_date"] == "2026-09-01"
    assert data["period"]["expenses"] == 750
    assert data["compared_to"]["expenses"] == 600
    assert data["change"]["expenses"] == {"amount": 150, "percent": 25.0}
    assert data["change"]["income"] == {"amount": 0, "percent": None}
    first = data["expenses_by_category"][0]
    assert first["category_id"] == categories["food"].id
    assert first["change"] == {"amount": 150, "percent": 150.0}


async def test_compare_with_explicit_period(
    db_session: AsyncSession, test_user: User
) -> None:
    await _seed_two_months(db_session, test_user)

    result, _ = await execute_tool(
        "compare_periods",
        {
            "date_filter_type": "date",
            "date": "2026-10-07",
            "compare_date_filter_type": "date",
            "compare_date": "2026-09-05",
        },
        db_session,
        test_user,
    )

    assert result["data"]["change"]["expenses"]["amount"] == 150


@pytest.mark.parametrize(
    "args",
    [
        {"date_filter_type": "month", "date": "2026-10", "compare_date": "2026-01"},
        {"date_filter_type": "month", "date": "October"},
        {"date_filter_type": "month", "date": "2026-10", "account_id": 999999},
    ],
    ids=["half-comparison", "bad-date", "foreign-account"],
)
async def test_compare_rejects_bad_input(
    db_session: AsyncSession, test_user: User, args
) -> None:
    result, _ = await execute_tool("compare_periods", args, db_session, test_user)
    assert result["success"] is False
    assert result["error"]


async def test_from_transaction_preview_labels_copied_fields(
    db_session: AsyncSession, test_user: User
) -> None:
    account = await create_test_account(db_session, owner_id=test_user.id, name="Card")
    category = await create_test_category(
        db_session, owner_id=test_user.id, name="Streaming"
    )
    expense = await create_test_expense(
        db_session,
        owner_id=test_user.id,
        account_id=account.id,
        category_id=category.id,
    )

    preview = await build_pending_preview(
        db_session,
        test_user,
        "create_recurring_from_transaction",
        {
            "transaction_type": "expense",
            "transaction_id": expense.id,
            "frequency": "monthly",
        },
    )

    assert preview["labels"] == {"account_id": "Card", "category_id": "Streaming"}
    assert preview["arguments"]["account"] == "Card"
    assert preview["arguments"]["category"] == "Streaming"
    assert "account_id" not in preview["arguments"]


@pytest.mark.parametrize(
    ("use_ai", "scans_left", "expected"),
    [
        (True, 1, ["AI provider", "only scan"]),
        (False, None, []),
    ],
)
async def test_detect_preview_warns_about_ai_and_the_free_scan(
    db_session: AsyncSession, test_user: User, monkeypatch, use_ai, scans_left, expected
) -> None:
    monkeypatch.setattr(
        recurring_tools.detection_service,
        "scans_remaining",
        AsyncMock(return_value=scans_left),
    )

    preview = await build_pending_preview(
        db_session, test_user, "detect_recurring", {"use_ai": use_ai}
    )
    message = format_confirmation_message("detect_recurring", preview)

    assert message.startswith("Approve a scan")
    for phrase in ("AI provider", "only scan"):
        assert (phrase in message) is (phrase in expected)


async def test_preview_goes_stale_when_the_series_changes(
    db_session: AsyncSession, test_user: User
) -> None:
    created = await _create(db_session, test_user)
    args = {"id": created["id"]}
    preview = await build_pending_preview(
        db_session, test_user, "skip_recurring_occurrence", args
    )
    pending = {
        "tool_name": "skip_recurring_occurrence",
        "tool_args": args,
        "preview": preview,
    }
    assert await preview_is_current(db_session, test_user, pending)

    await execute_tool(
        "update_recurring",
        {"id": created["id"], "data": {"amount": 99}, "response_language": "en"},
        db_session,
        test_user,
    )

    assert not await preview_is_current(db_session, test_user, pending)
