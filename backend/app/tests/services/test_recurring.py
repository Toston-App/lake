"""Generation of recurring transactions: worker claiming, idempotency and failures."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from app.models import Expense, RecurringTransaction, Transfer, User
from app.services import recurring as recurring_service
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from tests.utils import create_test_account

TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )


async def _create(db: AsyncSession, user: User, **fields) -> RecurringTransaction:
    data = {
        "type": "expense",
        "amount": 10,
        "frequency": "daily",
        "start_date": TODAY + timedelta(days=1),
        "timezone": "UTC",
    }
    data.update(fields)
    return await recurring_service.create_recurring(
        db, user, schemas.RecurringTransactionCreate(**data)
    )


async def _count(db: AsyncSession, model, recurring_id: int) -> int:
    result = await db.execute(
        select(model.id).where(model.recurring_id == recurring_id)
    )
    return len(result.scalars().all())


async def test_generate_due_is_idempotent(db_session: AsyncSession, test_user: User):
    template = await _create(db_session, test_user)
    later = TODAY + timedelta(days=3)

    assert (
        await recurring_service.generate_due(db_session, template.id, today=later) == 3
    )
    assert (
        await recurring_service.generate_due(db_session, template.id, today=later) == 0
    )
    assert await _count(db_session, Expense, template.id) == 3


async def test_generate_due_skips_occurrence_created_before_a_crash(
    db_session: AsyncSession, test_user: User
):
    template = await _create(db_session, test_user)
    # The transaction exists but the template was not advanced.
    db_session.add(
        Expense(
            owner_id=test_user.id,
            amount=10,
            date=template.next_run_date,
            recurring_id=template.id,
        )
    )
    await db_session.commit()

    await recurring_service.generate_due(
        db_session, template.id, today=template.next_run_date
    )
    assert await _count(db_session, Expense, template.id) == 1


async def test_catch_up_is_capped_per_run(db_session: AsyncSession, test_user: User):
    template = await _create(db_session, test_user)
    later = TODAY + timedelta(days=10)

    assert (
        await recurring_service.generate_due(
            db_session, template.id, today=later, limit=4
        )
        == 4
    )
    assert (
        await recurring_service.generate_due(
            db_session, template.id, today=later, limit=60
        )
        == 6
    )


async def test_claim_due_leases_only_due_templates(
    db_session: AsyncSession, test_user: User
):
    due = await _create(db_session, test_user)
    not_due = await _create(
        db_session, test_user, start_date=TODAY + timedelta(days=30)
    )
    paused = await _create(db_session, test_user)
    for template in (due, paused):
        template.next_run_date = TODAY - timedelta(days=1)
    paused.is_active = False
    await db_session.commit()

    claimed = await recurring_service.claim_due(db_session)
    assert due.id in claimed
    assert not_due.id not in claimed
    assert paused.id not in claimed
    # Leased templates are not claimed twice.
    assert due.id not in await recurring_service.claim_due(db_session)


async def test_leased_template_is_not_generated_by_requests(
    db_session: AsyncSession, test_user: User
):
    template = await _create(db_session, test_user)
    template.locked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    created = await recurring_service.generate_due(
        db_session, template.id, today=TODAY + timedelta(days=2)
    )
    assert created == 0


async def test_failure_pauses_template_with_reason(
    db_session: AsyncSession, test_user: User
):
    checking = await create_test_account(db_session, owner_id=test_user.id)
    savings = await create_test_account(
        db_session, owner_id=test_user.id, name="Savings"
    )
    template = await _create(
        db_session,
        test_user,
        type="transfer",
        account_id=checking.id,
        to_account_id=savings.id,
    )
    await db_session.delete(savings)
    await db_session.commit()

    created = await recurring_service.generate_due(
        db_session, template.id, today=TODAY + timedelta(days=2)
    )
    template = await db_session.get(
        RecurringTransaction, template.id, populate_existing=True
    )
    assert created == 0
    assert template.is_active is False
    assert template.last_error
    assert template.locked_until is None
    assert await _count(db_session, Transfer, template.id) == 0


async def test_weekend_adjustment_records_friday(
    db_session: AsyncSession, test_user: User
):
    # Saturday 15 Nov 2031 -> Friday 14.
    template = await _create(
        db_session,
        test_user,
        frequency="monthly",
        start_date=date(2031, 11, 1),
        month_days=[15],
        business_day_adjustment="previous",
    )
    assert template.next_run_date == date(2031, 11, 14)
    await recurring_service.generate_due(
        db_session, template.id, today=date(2031, 11, 14)
    )
    expense = (
        await db_session.execute(
            select(Expense).where(Expense.recurring_id == template.id)
        )
    ).scalar_one()
    assert expense.date == date(2031, 11, 14)
    template = await db_session.get(
        RecurringTransaction, template.id, populate_existing=True
    )
    assert template.next_run_date == date(2031, 12, 15)
