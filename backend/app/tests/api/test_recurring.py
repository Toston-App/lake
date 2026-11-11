"""
API endpoint tests for /api/v1/recurring.

Covers create (with immediate catch-up), preview, mark-as-recurring, update,
pause/resume, skip, delete (keeping or removing generated transactions),
validation and ownership.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from app.models import (
    Account,
    Category,
    Expense,
    Income,
    RecurringTransaction,
    Transfer,
    User,
)
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.crud import crud_transaction
from fastapi_pagination import Params
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

    async def test_sunday_start_moves_to_previous_friday(self, client: AsyncClient):
        resp = await client.post(
            f"{PREFIX}/preview",
            json={
                "frequency": "monthly",
                "start_date": "2036-06-15",
                "business_day_adjustment": "previous",
            },
        )
        assert resp.status_code == 200
        upcoming = resp.json()["upcoming"]
        assert upcoming[0] == {"nominal_date": "2036-06-15", "date": "2036-06-13"}
        assert upcoming[1] == {"nominal_date": "2036-07-15", "date": "2036-07-15"}

    async def test_exclude_start_matches_mark_as_recurring(self, client: AsyncClient):
        # Saturday 17 Oct 2026 is already recorded; Monday 19 is still pending.
        resp = await client.post(
            f"{PREFIX}/preview",
            json={
                "frequency": "weekly",
                "weekdays": [0, 5],
                "start_date": "2026-10-17",
                "business_day_adjustment": "next",
                "exclude_start": True,
            },
        )
        assert resp.status_code == 200
        assert [o["date"] for o in resp.json()["upcoming"][:2]] == [
            "2026-10-19",
            "2026-10-26",
        ]


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

    async def test_overrides_fields_of_the_transaction(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        account = await create_test_account(db_session, owner_id=test_user.id)
        other = await create_test_account(
            db_session, owner_id=test_user.id, name="Card"
        )
        expense = await create_test_expense(
            db_session,
            owner_id=test_user.id,
            amount=10,
            description="Gym",
            account_id=account.id,
            expense_date=TODAY - timedelta(days=1),
        )
        resp = await client.post(
            f"{PREFIX}/from-transaction",
            json={
                "transaction_type": "expense",
                "transaction_id": expense.id,
                "frequency": "monthly",
                "amount": 12.5,
                "account_id": other.id,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["amount"] == 12.5
        assert data["account_id"] == other.id
        assert data["description"] == "Gym"
        assert data["start_date"] == (TODAY - timedelta(days=1)).isoformat()

    async def test_saturday_transaction_moved_to_monday_is_not_duplicated(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        saturday = date(2026, 10, 17)
        expense = await create_test_expense(
            db_session, owner_id=test_user.id, expense_date=saturday
        )
        resp = await client.post(
            f"{PREFIX}/from-transaction",
            json={
                "transaction_type": "expense",
                "transaction_id": expense.id,
                "frequency": "monthly",
                "business_day_adjustment": "next",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["next_run_date"] == "2026-11-17"
        assert len(await _expenses(db_session, resp.json()["id"])) == 1

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
        # Reported as missing, so another user's ids can't be probed.
        assert resp.status_code == 404

    async def _templates(self, db: AsyncSession, user: User) -> list[int]:
        result = await db.execute(
            select(RecurringTransaction.id).where(
                RecurringTransaction.owner_id == user.id
            )
        )
        return list(result.scalars().all())

    async def test_marking_a_linked_transaction_again_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        expense = await create_test_expense(
            db_session, owner_id=test_user.id, expense_date=TODAY - timedelta(days=1)
        )
        body = {
            "transaction_type": "expense",
            "transaction_id": expense.id,
            "frequency": "monthly",
        }
        first = await client.post(f"{PREFIX}/from-transaction", json=body)
        assert first.status_code == 200, first.text
        again = await client.post(f"{PREFIX}/from-transaction", json=body)
        assert again.status_code == 400
        assert "already belongs" in again.json()["detail"]
        assert await self._templates(db_session, test_user) == [first.json()["id"]]

    async def test_mark_linked_by_a_concurrent_request_conflicts(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_user: User,
        monkeypatch: pytest.MonkeyPatch,
    ):
        expense = await create_test_expense(
            db_session, owner_id=test_user.id, expense_date=TODAY - timedelta(days=1)
        )
        winner = await client.post(PREFIX, json=_weekly_expense())
        assert winner.status_code == 200, winner.text
        winner_id = winner.json()["id"]
        get_expense = crud.expense.get

        async def linked_meanwhile(db, transaction_id):
            # The request reads the expense unlinked, then the concurrent
            # request links it before this one writes.
            transaction = await get_expense(db, transaction_id)
            await db.execute(
                update(Expense)
                .where(Expense.id == transaction_id)
                .values(recurring_id=winner_id)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            return transaction

        monkeypatch.setattr(crud.expense, "get", linked_meanwhile)
        resp = await client.post(
            f"{PREFIX}/from-transaction",
            json={
                "transaction_type": "expense",
                "transaction_id": expense.id,
                "frequency": "monthly",
            },
        )
        assert resp.status_code == 409, resp.text
        assert await self._templates(db_session, test_user) == [winner_id]
        expense = await _reload(db_session, Expense, expense.id)
        assert expense.recurring_id == winner_id


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

    @pytest.mark.parametrize(
        "field",
        [
            "frequency",
            "interval",
            "weekdays",
            "monthly_mode",
            "month_days",
            "business_day_adjustment",
            "start_date",
            "timezone",
            "is_active",
        ],
    )
    async def test_cannot_null_non_nullable_fields(
        self, client: AsyncClient, field: str
    ):
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        resp = await client.put(f"{PREFIX}/{created['id']}", json={field: None})
        assert resp.status_code == 400
        assert resp.json()["detail"] == f"{field} cannot be empty"

    async def test_skip_next(self, client: AsyncClient):
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        resp = await client.post(f"{PREFIX}/{created['id']}/skip")
        assert resp.status_code == 200
        assert resp.json()["next_run_date"] == (TODAY + timedelta(days=14)).isoformat()
        assert resp.json()["occurrences_count"] == 3

    async def test_changes_conflict_while_generating(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from app.models import RecurringTransaction
        from app.services import recurring as recurring_service

        monkeypatch.setattr(recurring_service, "LEASE_WAIT_SECONDS", 0)
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        template = await _reload(db_session, RecurringTransaction, created["id"])
        template.locked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        await db_session.commit()

        url = f"{PREFIX}/{created['id']}"
        assert (await client.post(f"{url}/skip")).status_code == 409
        assert (await client.put(url, json={"is_active": False})).status_code == 409
        assert (await client.delete(url)).status_code == 409


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

    async def test_delete_generated_keeps_transaction_marked_as_recurring(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        account = await create_test_account(
            db_session, owner_id=test_user.id, initial_balance=100
        )
        source = await client.post(
            "/api/v1/expenses",
            json={
                "amount": 10,
                "description": "Gym",
                "date": (TODAY - timedelta(days=14)).isoformat(),
                "account_id": account.id,
            },
        )
        assert source.status_code == 200, source.text
        source_id = source.json()["id"]
        created = await client.post(
            f"{PREFIX}/from-transaction",
            json={
                "transaction_type": "expense",
                "transaction_id": source_id,
                "frequency": "weekly",
                "timezone": "UTC",
            },
        )
        assert created.status_code == 200, created.text
        recurring_id = created.json()["id"]
        assert len(await _expenses(db_session, recurring_id)) == 3
        assert (await _reload(db_session, Account, account.id)).current_balance == 70

        resp = await client.delete(
            f"{PREFIX}/{recurring_id}", params={"delete_generated": True}
        )
        assert resp.status_code == 200
        assert await _expenses(db_session, recurring_id) == []
        kept = await _reload(db_session, Expense, source_id)
        assert kept is not None
        assert kept.recurring_id is None
        assert (await _reload(db_session, Account, account.id)).current_balance == 90

    async def test_delete_generated_keeps_transfer_marked_as_recurring(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        checking = await create_test_account(
            db_session, owner_id=test_user.id, initial_balance=1000
        )
        savings = await create_test_account(
            db_session, owner_id=test_user.id, name="Savings"
        )
        source = await client.post(
            "/api/v1/transfers",
            json={
                "amount": 100,
                "date": (TODAY - timedelta(days=14)).isoformat(),
                "from_acc": checking.id,
                "to_acc": savings.id,
            },
        )
        assert source.status_code == 200, source.text
        source_id = source.json()["id"]
        created = await client.post(
            f"{PREFIX}/from-transaction",
            json={
                "transaction_type": "transfer",
                "transaction_id": source_id,
                "frequency": "weekly",
                "timezone": "UTC",
            },
        )
        assert created.status_code == 200, created.text
        assert (await _reload(db_session, Account, savings.id)).current_balance == 300

        resp = await client.delete(
            f"{PREFIX}/{created.json()['id']}", params={"delete_generated": True}
        )
        assert resp.status_code == 200
        kept = await _reload(db_session, Transfer, source_id)
        assert kept is not None
        assert kept.recurring_id is None
        assert (await _reload(db_session, Account, checking.id)).current_balance == 900
        assert (await _reload(db_session, Account, savings.id)).current_balance == 100


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
        # Reported as missing, so another user's ids can't be probed.
        url = f"{PREFIX}/{foreign.id}"
        assert (await client.get(url)).status_code == 404
        assert (await client.delete(url)).status_code == 404
        assert (await client.put(url, json={"amount": 5})).status_code == 404
        assert (await client.post(f"{url}/skip")).status_code == 404
        assert (await client.get(f"{PREFIX}/999999")).status_code == 404

    async def test_transaction_schemas_expose_recurring_id(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        from app.schemas.transaction import ExpenseTransaction

        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        expense = (await _expenses(db_session, created["id"]))[0]
        assert ExpenseTransaction.model_validate(expense).recurring_id == created["id"]
        resp = await client.get(f"/api/v1/expenses/{expense.id}")
        assert resp.json()["recurring_id"] == created["id"]


def _years_ago(years: int, days: int = 0) -> date:
    from app.services.recurring import _shift_years

    return _shift_years(TODAY, -years) + timedelta(days=days)


class TestLimits:
    async def test_start_date_at_most_two_years_back(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        too_old = _years_ago(2, days=-1)
        resp = await client.post(
            PREFIX, json=_weekly_expense(start_date=too_old.isoformat())
        )
        assert resp.status_code == 400
        assert "2 years" in resp.json()["detail"]
        preview = await client.post(
            f"{PREFIX}/preview",
            json={"frequency": "yearly", "start_date": too_old.isoformat()},
        )
        assert preview.status_code == 400

        oldest = _years_ago(2, days=1)
        resp = await client.post(
            PREFIX,
            json=_weekly_expense(frequency="yearly", start_date=oldest.isoformat()),
        )
        assert resp.status_code == 200, resp.text
        assert len(await _expenses(db_session, resp.json()["id"])) == 2

    async def test_update_checks_only_changed_dates(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        from app.models import RecurringTransaction

        # A series that started long ago (it aged past the limit) stays editable.
        old = RecurringTransaction(
            owner_id=test_user.id,
            type="expense",
            amount=1,
            frequency="yearly",
            start_date=_years_ago(5),
            is_active=False,
        )
        db_session.add(old)
        await db_session.commit()
        url = f"{PREFIX}/{old.id}"
        assert (await client.put(url, json={"amount": 2})).status_code == 200
        # The dashboard sends the whole form back, unchanged start date included.
        resp = await client.put(
            url, json={"amount": 3, "start_date": _years_ago(5).isoformat()}
        )
        assert resp.status_code == 200, resp.text
        resp = await client.put(url, json={"start_date": _years_ago(3).isoformat()})
        assert resp.status_code == 400

    async def test_far_future_dates_are_rejected(self, client: AsyncClient):
        for payload in [
            _weekly_expense(frequency="monthly", start_date="9999-12-20"),
            _weekly_expense(start_date=_years_ago(-11).isoformat()),
            _weekly_expense(end_date=_years_ago(-11).isoformat()),
        ]:
            assert (await client.post(PREFIX, json=payload)).status_code == 400
        preview = await client.post(
            f"{PREFIX}/preview",
            json={"frequency": "monthly", "start_date": "9999-12-20"},
        )
        assert preview.status_code == 400
        # Nothing was saved that would break the list.
        listed = await client.get(PREFIX)
        assert listed.status_code == 200
        assert listed.json() == []

    async def test_series_ending_at_year_9999_stops(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        from app.models import RecurringTransaction

        # Stored before the date limits existed: the list must still load.
        db_session.add(
            RecurringTransaction(
                owner_id=test_user.id,
                type="expense",
                amount=1,
                frequency="monthly",
                start_date=date(9999, 12, 20),
                next_nominal_date=date(9999, 12, 20),
                next_run_date=date(9999, 12, 20),
            )
        )
        await db_session.commit()
        listed = await client.get(PREFIX)
        assert listed.status_code == 200
        assert [o["date"] for o in listed.json()[0]["upcoming"]] == ["9999-12-20"]

    async def test_only_iana_timezones(self, client: AsyncClient):
        for tz in ("posixrules", "utc", "Europe/../UTC", "localtime"):
            resp = await client.post(PREFIX, json=_weekly_expense(timezone=tz))
            assert resp.status_code == 422, tz
        resp = await client.post(
            PREFIX, json=_weekly_expense(timezone="America/Mexico_City")
        )
        assert resp.status_code == 200

    async def test_amount_must_be_finite_and_bounded(self, client: AsyncClient):
        import json

        for amount in ("Infinity", "NaN", "1e13"):
            body = json.dumps(_weekly_expense()).replace(
                '"amount": 10', f'"amount": {amount}'
            )
            resp = await client.post(
                PREFIX, content=body, headers={"content-type": "application/json"}
            )
            assert resp.status_code == 422, amount
        created = (await client.post(PREFIX, json=_weekly_expense())).json()
        body = '{"amount": Infinity}'
        resp = await client.put(
            f"{PREFIX}/{created['id']}",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 422

    async def test_description_length_is_limited(self, client: AsyncClient):
        resp = await client.post(PREFIX, json=_weekly_expense(description="x" * 501))
        assert resp.status_code == 422

    async def test_templates_per_user_are_capped(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        from app.services import recurring as recurring_service

        monkeypatch.setattr(recurring_service, "MAX_TEMPLATES_PER_USER", 1)
        future = (TODAY + timedelta(days=3)).isoformat()
        payload = _weekly_expense(start_date=future)
        assert (await client.post(PREFIX, json=payload)).status_code == 200
        resp = await client.post(PREFIX, json=payload)
        assert resp.status_code == 400
        assert "at most 1" in resp.json()["detail"]

    async def test_mark_as_recurring_up_to_two_years_back(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        def body(expense: Expense) -> dict:
            return {
                "transaction_type": "expense",
                "transaction_id": expense.id,
                "frequency": "yearly",
            }

        old = await create_test_expense(
            db_session, owner_id=test_user.id, expense_date=_years_ago(2, days=-1)
        )
        resp = await client.post(f"{PREFIX}/from-transaction", json=body(old))
        assert resp.status_code == 400
        assert "last 2 years" in resp.json()["detail"]

        recent = await create_test_expense(
            db_session, owner_id=test_user.id, expense_date=_years_ago(2, days=1)
        )
        resp = await client.post(f"{PREFIX}/from-transaction", json=body(recent))
        assert resp.status_code == 200, resp.text


class TestTransactionsRecurringFilter:
    async def test_filters_transactions_by_recurring_series(
        self, client: AsyncClient, db_session: AsyncSession, test_user: User
    ):
        resp = await client.post(PREFIX, json=_weekly_expense())
        assert resp.status_code == 200, resp.text
        await create_test_expense(
            db_session, owner_id=test_user.id, description="One-off"
        )

        async def search(recurring: bool | None):
            return await crud_transaction.get_multi_by_owner_with_filters(
                db_session,
                owner_id=test_user.id,
                recurring=recurring,
                params=Params(page=1, size=50),
            )

        everything = await search(None)
        recurring = await search(True)
        one_off = await search(False)

        assert everything.total == 4
        assert recurring.total == 3
        assert {t.description for t in recurring.items} == {"Gym"}
        assert [t.description for t in one_off.items] == ["One-off"]
