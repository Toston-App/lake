"""
API endpoint tests for /api/v1/recurring.

Covers create (with immediate catch-up), preview, mark-as-recurring, update,
pause/resume, skip, delete (keeping or removing generated transactions),
validation and ownership.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from app.models import Account, Category, Expense, Income, Transfer, User
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_expense,
    create_test_subcategory,
    create_test_user,
)

PREFIX = "/api/v1/recurring"
TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )


async def _expenses(db: AsyncSession, recurring_id: int) -> list[Expense]:
    result = await db.execute(
        select(Expense)
        .where(Expense.recurring_id == recurring_id)
        .order_by(Expense.date)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def _reload(db: AsyncSession, model, obj_id: int):
    return await db.get(model, obj_id, populate_existing=True)


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


class TestCreateRecurring:
    async def test_creates_missed_occurrences_and_updates_balances(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        account = await create_test_account(
            db_session, owner_id=test_user.id, initial_balance=100
        )
        category = await create_test_category(db_session, owner_id=test_user.id)
        resp = await client.post(
            PREFIX,
            json=_weekly_expense(account_id=account.id, category_id=category.id),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["occurrences_count"] == 3
        assert data["last_run_date"] == TODAY.isoformat()
        assert data["next_run_date"] == (TODAY + timedelta(days=7)).isoformat()
        assert len(data["upcoming"]) == 5

        expenses = await _expenses(db_session, data["id"])
        assert [e.date for e in expenses] == [
            TODAY - timedelta(days=14),
            TODAY - timedelta(days=7),
            TODAY,
        ]
        assert all(e.made_from == "Recurring" for e in expenses)
        assert (await _reload(db_session, Account, account.id)).current_balance == 70
        assert (await _reload(db_session, Category, category.id)).total == 30
        assert (await _reload(db_session, User, test_user.id)).balance_outcome == 30

    async def test_future_start_creates_nothing_yet(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        start = TODAY + timedelta(days=3)
        resp = await client.post(
            PREFIX, json=_weekly_expense(start_date=start.isoformat())
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["occurrences_count"] == 0
        assert data["next_run_date"] == start.isoformat()
        assert await _expenses(db_session, data["id"]) == []

    async def test_income_and_transfer(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        checking = await create_test_account(db_session, owner_id=test_user.id)
        savings = await create_test_account(
            db_session, owner_id=test_user.id, name="Savings"
        )
        income = await client.post(
            PREFIX,
            json={
                "type": "income",
                "amount": 1000,
                "frequency": "monthly",
                "start_date": TODAY.isoformat(),
                "account_id": checking.id,
            },
        )
        transfer = await client.post(
            PREFIX,
            json={
                "type": "transfer",
                "amount": 200,
                "frequency": "monthly",
                "start_date": TODAY.isoformat(),
                "account_id": checking.id,
                "to_account_id": savings.id,
            },
        )
        assert income.status_code == 200, income.text
        assert transfer.status_code == 200, transfer.text
        incomes = (
            (
                await db_session.execute(
                    select(Income).where(Income.recurring_id == income.json()["id"])
                )
            )
            .scalars()
            .all()
        )
        transfers = (
            (
                await db_session.execute(
                    select(Transfer).where(
                        Transfer.recurring_id == transfer.json()["id"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(incomes) == 1
        assert len(transfers) == 1
        assert (await _reload(db_session, Account, checking.id)).current_balance == 800
        assert (await _reload(db_session, Account, savings.id)).current_balance == 200

    async def test_transfer_needs_two_different_accounts(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        resp = await client.post(
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
        assert resp.status_code == 400

    async def test_rejects_other_users_account(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other = await create_test_user(db_session, email="other@example.com")
        account = await create_test_account(db_session, owner_id=other.id)
        resp = await client.post(PREFIX, json=_weekly_expense(account_id=account.id))
        assert resp.status_code == 404

    async def test_rejects_subcategory_from_another_category(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        food = await create_test_category(db_session, owner_id=test_user.id)
        home = await create_test_category(
            db_session, owner_id=test_user.id, name="Home"
        )
        rent = await create_test_subcategory(
            db_session, owner_id=test_user.id, category_id=home.id
        )
        resp = await client.post(
            PREFIX,
            json=_weekly_expense(category_id=food.id, subcategory_id=rent.id),
        )
        assert resp.status_code == 400

    async def test_validates_rule_fields(self, client: AsyncClient):
        bad_payloads = [
            _weekly_expense(amount=0),
            _weekly_expense(weekdays=[7]),
            _weekly_expense(frequency="semimonthly", month_days=[15]),
            _weekly_expense(frequency="monthly", month_days=[32]),
            _weekly_expense(timezone="Mars/Olympus"),
            _weekly_expense(end_date=(TODAY - timedelta(days=30)).isoformat()),
            _weekly_expense(
                frequency="monthly", monthly_mode="nth_weekday", weekdays=[4]
            ),
        ]
        for payload in bad_payloads:
            resp = await client.post(PREFIX, json=payload)
            assert resp.status_code in (400, 422), payload

    async def test_max_occurrences_ends_series(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        resp = await client.post(PREFIX, json=_weekly_expense(max_occurrences=2))
        data = resp.json()
        assert data["occurrences_count"] == 2
        assert data["next_run_date"] is None
        assert data["upcoming"] == []
        assert len(await _expenses(db_session, data["id"])) == 2


class TestPreview:
    async def test_quincena_moves_saturday_to_friday(self, client: AsyncClient):
        resp = await client.post(
            f"{PREFIX}/preview",
            json={
                "frequency": "semimonthly",
                "start_date": "2025-02-01",
                "business_day_adjustment": "previous",
            },
        )
        assert resp.status_code == 200
        upcoming = resp.json()["upcoming"]
        assert upcoming[0] == {"nominal_date": "2025-02-15", "date": "2025-02-14"}
        assert upcoming[1] == {"nominal_date": "2025-02-28", "date": "2025-02-28"}


class TestFromTransaction:
    async def test_marks_existing_expense_as_recurring(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        expense = await create_test_expense(
            db_session,
            owner_id=test_user.id,
            amount=15.99,
            description="Netflix",
            account_id=account.id,
            expense_date=TODAY - timedelta(days=1),
        )
        resp = await client.post(
            f"{PREFIX}/from-transaction",
            json={
                "transaction_type": "expense",
                "transaction_id": expense.id,
                "frequency": "monthly",
                "timezone": "UTC",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["amount"] == 15.99
        assert data["description"] == "Netflix"
        assert data["account_id"] == account.id
        assert data["occurrences_count"] == 1
        assert data["next_run_date"] > TODAY.isoformat()
        linked = await _expenses(db_session, data["id"])
        assert [e.id for e in linked] == [expense.id]

        again = await client.post(
            f"{PREFIX}/from-transaction",
            json={
                "transaction_type": "expense",
                "transaction_id": expense.id,
                "frequency": "monthly",
            },
        )
        assert again.status_code == 400

    async def test_cannot_mark_other_users_transaction(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        other = await create_test_user(db_session, email="other@example.com")
        expense = await create_test_expense(db_session, owner_id=other.id)
        resp = await client.post(
            f"{PREFIX}/from-transaction",
            json={
                "transaction_type": "expense",
                "transaction_id": expense.id,
                "frequency": "monthly",
            },
        )
        assert resp.status_code == 403


class TestUpdateRecurring:
    async def test_amount_change_only_affects_future(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        resp = await client.put(f"{PREFIX}/{created['id']}", json={"amount": 25})
        assert resp.status_code == 200
        assert resp.json()["amount"] == 25
        assert {e.amount for e in await _expenses(db_session, created["id"])} == {10}

    async def test_schedule_change_does_not_backfill(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        resp = await client.put(
            f"{PREFIX}/{created['id']}", json={"frequency": "daily"}
        )
        data = resp.json()
        assert data["occurrences_count"] == 3
        assert data["next_run_date"] == (TODAY + timedelta(days=1)).isoformat()
        assert len(await _expenses(db_session, created["id"])) == 3

    async def test_pause_and_resume_skips_missed(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        created = (
            await client.post(
                PREFIX,
                json=_weekly_expense(
                    start_date=(TODAY + timedelta(days=1)).isoformat()
                ),
            )
        ).json()
        paused = await client.put(
            f"{PREFIX}/{created['id']}", json={"is_active": False}
        )
        assert paused.json()["is_active"] is False

        resumed = await client.put(
            f"{PREFIX}/{created['id']}", json={"is_active": True}
        )
        data = resumed.json()
        assert data["is_active"] is True
        assert data["next_run_date"] == (TODAY + timedelta(days=1)).isoformat()

    async def test_cannot_clear_required_fields(self, client: AsyncClient):
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        resp = await client.put(f"{PREFIX}/{created['id']}", json={"amount": None})
        assert resp.status_code == 400

    async def test_skip_next(self, client: AsyncClient):
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        resp = await client.post(f"{PREFIX}/{created['id']}/skip")
        assert resp.status_code == 200
        assert resp.json()["next_run_date"] == (TODAY + timedelta(days=14)).isoformat()
        assert resp.json()["occurrences_count"] == 3


class TestDeleteRecurring:
    async def test_delete_keeps_generated_transactions(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        ids = [e.id for e in await _expenses(db_session, created["id"])]

        resp = await client.delete(f"{PREFIX}/{created['id']}")
        assert resp.status_code == 200
        remaining = (
            (
                await db_session.execute(
                    select(Expense)
                    .where(Expense.id.in_(ids))
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        assert len(remaining) == 3
        assert all(e.recurring_id is None for e in remaining)
        assert (await client.get(f"{PREFIX}/{created['id']}")).status_code == 404

    async def test_delete_generated_reverses_balances(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        account = await create_test_account(
            db_session, owner_id=test_user.id, initial_balance=100
        )
        created = (
            await client.post(PREFIX, json=_weekly_expense(account_id=account.id))
        ).json()
        assert (await _reload(db_session, Account, account.id)).current_balance == 70

        resp = await client.delete(
            f"{PREFIX}/{created['id']}", params={"delete_generated": True}
        )
        assert resp.status_code == 200
        assert await _expenses(db_session, created["id"]) == []
        assert (await _reload(db_session, Account, account.id)).current_balance == 100


class TestReadRecurring:
    async def test_list_and_ownership(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        listed = await client.get(PREFIX)
        assert [t["id"] for t in listed.json()] == [created["id"]]

        other = await create_test_user(db_session, email="other@example.com")
        account = await create_test_account(db_session, owner_id=other.id)
        from app.models import RecurringTransaction

        foreign = RecurringTransaction(
            owner_id=other.id,
            type="expense",
            amount=1,
            account_id=account.id,
            frequency="monthly",
            start_date=TODAY,
        )
        db_session.add(foreign)
        await db_session.commit()
        assert (await client.get(f"{PREFIX}/{foreign.id}")).status_code == 403
        assert (await client.delete(f"{PREFIX}/{foreign.id}")).status_code == 403

    async def test_transaction_schemas_expose_recurring_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.schemas.transaction import ExpenseTransaction

        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        expense = (await _expenses(db_session, created["id"]))[0]
        assert ExpenseTransaction.model_validate(expense).recurring_id == created["id"]
        resp = await client.get(f"/api/v1/expenses/{expense.id}")
        assert resp.json()["recurring_id"] == created["id"]
