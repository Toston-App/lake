"""Generation of recurring transactions: worker claiming, idempotency and failures."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.models import Expense, Income, RecurringTransaction, Transfer, User
from app.services import accounts as account_service
from app.services import catalog as catalog_service
from app.services import recurring as recurring_service
from app.services.errors import ConflictError
from app.worker import recurring_worker
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_place,
    create_test_subcategory,
    create_test_user,
)

TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.services.accounts.invalidate_user_cache", AsyncMock(return_value=True)
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


async def test_generate_due_does_not_count_already_generated_occurrence(
    db_session: AsyncSession, test_user: User
):
    template = await _create(db_session, test_user)
    first_run = template.next_run_date
    db_session.add(
        Expense(
            owner_id=test_user.id,
            amount=10,
            date=first_run,
            recurring_id=template.id,
        )
    )
    await db_session.commit()

    assert (
        await recurring_service.generate_due(db_session, template.id, today=first_run)
        == 0
    )
    await db_session.refresh(template)
    assert template.last_run_date == first_run
    assert template.next_run_date == first_run + timedelta(days=1)

    # The next occurrence is still created and counted.
    assert (
        await recurring_service.generate_due(
            db_session, template.id, today=first_run + timedelta(days=1)
        )
        == 1
    )
    assert await _count(db_session, Expense, template.id) == 2


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


async def test_lease_is_renewed_per_occurrence_and_released(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    template = await _create(db_session, test_user)
    seen = []
    create_occurrence = recurring_service._create_occurrence

    async def record_lease(db, owner, tmpl, run_date):
        seen.append(
            await db.scalar(
                select(RecurringTransaction.locked_until).where(
                    RecurringTransaction.id == tmpl.id
                )
            )
        )
        await create_occurrence(db, owner, tmpl, run_date)

    monkeypatch.setattr(recurring_service, "_create_occurrence", record_lease)
    created = await recurring_service.generate_due(
        db_session, template.id, today=TODAY + timedelta(days=3)
    )
    template = await db_session.get(
        RecurringTransaction, template.id, populate_existing=True
    )
    assert created == 3
    assert all(seen) and seen == sorted(set(seen))
    assert template.locked_until is None


async def test_expired_lease_taken_over_stops_generation(
    db_session: AsyncSession, test_user: User
):
    template = await _create(db_session, test_user)
    template.next_run_date = TODAY - timedelta(days=1)
    template.next_nominal_date = template.next_run_date
    await db_session.commit()
    stale = (await recurring_service.claim_due(db_session))[template.id]
    # The lease expired mid-batch and a request (or another worker) took it.
    other = datetime.now(timezone.utc) + timedelta(minutes=5)
    template.locked_until = other
    await db_session.commit()

    created = await recurring_service.generate_due(db_session, template.id, lease=stale)
    template = await db_session.get(
        RecurringTransaction, template.id, populate_existing=True
    )
    assert created == 0
    assert await _count(db_session, Expense, template.id) == 0
    # The new holder's lease is left alone.
    assert template.locked_until == other


async def test_lease_lost_between_occurrences_stops_generation(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    template = await _create(db_session, test_user)
    other = datetime.now(timezone.utc) + timedelta(minutes=5)
    create_occurrence = recurring_service._create_occurrence

    async def create_then_lose_lease(db, owner, tmpl, run_date):
        await create_occurrence(db, owner, tmpl, run_date)
        tmpl.locked_until = other
        await db.commit()

    monkeypatch.setattr(recurring_service, "_create_occurrence", create_then_lose_lease)
    created = await recurring_service.generate_due(
        db_session, template.id, today=TODAY + timedelta(days=3)
    )
    template = await db_session.get(
        RecurringTransaction, template.id, populate_existing=True
    )
    assert created == 1
    assert await _count(db_session, Expense, template.id) == 1
    assert template.locked_until == other


async def test_user_changes_wait_for_a_generation_run(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(recurring_service, "LEASE_WAIT_SECONDS", 0)
    template = await _create(db_session, test_user)
    next_run_date = template.next_run_date
    # A worker is generating this template.
    template.locked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    with pytest.raises(ConflictError):
        await recurring_service.skip_next(db_session, test_user, template.id)
    with pytest.raises(ConflictError):
        await recurring_service.update_recurring(
            db_session,
            test_user,
            template.id,
            schemas.RecurringTransactionUpdate(is_active=False),
        )
    with pytest.raises(ConflictError):
        await recurring_service.delete_recurring(db_session, test_user, template.id)

    template = await db_session.get(
        RecurringTransaction, template.id, populate_existing=True
    )
    assert template.is_active is True
    assert template.next_run_date == next_run_date


async def test_user_changes_hold_and_release_the_lease(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    template_id = (await _create(db_session, test_user)).id
    leased_during_change = []
    advance = recurring_service._advance

    def spy(t: RecurringTransaction) -> None:
        leased_during_change.append(t.locked_until is not None)
        advance(t)

    monkeypatch.setattr(recurring_service, "_advance", spy)
    await recurring_service.skip_next(db_session, test_user, template_id)
    assert leased_during_change == [True]

    template = await db_session.get(
        RecurringTransaction, template_id, populate_existing=True
    )
    assert template.locked_until is None

    template = await recurring_service.update_recurring(
        db_session,
        test_user,
        template_id,
        schemas.RecurringTransactionUpdate(is_active=False),
    )
    assert template.is_active is False
    assert template.locked_until is None


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


async def _refresh(db: AsyncSession, template: RecurringTransaction):
    return await db.get(RecurringTransaction, template.id, populate_existing=True)


async def test_deleting_the_account_pauses_an_expense_template(
    db_session: AsyncSession, test_user: User
):
    checking = await create_test_account(db_session, owner_id=test_user.id)
    rent = await _create(db_session, test_user, account_id=checking.id)
    await account_service.delete_account(db_session, test_user, checking.id)

    created = await recurring_service.generate_due(
        db_session, rent.id, today=TODAY + timedelta(days=2)
    )
    rent = await _refresh(db_session, rent)
    assert created == 0
    assert rent.is_active is False
    assert rent.account_id is None
    assert "account" in rent.last_error
    assert await _count(db_session, Expense, rent.id) == 0


async def test_deleting_the_subcategory_or_place_pauses_an_income_template(
    db_session: AsyncSession, test_user: User
):
    category = await create_test_category(
        db_session, owner_id=test_user.id, is_income=True
    )
    salary = await create_test_subcategory(
        db_session, owner_id=test_user.id, category_id=category.id
    )
    office = await create_test_place(db_session, owner_id=test_user.id)
    by_subcategory = await _create(
        db_session, test_user, type="income", subcategory_id=salary.id
    )
    by_place = await _create(db_session, test_user, type="income", place_id=office.id)
    await catalog_service.delete_subcategory(db_session, test_user, salary.id)
    await catalog_service.delete_place(db_session, test_user, office.id)

    for template, kind in ((by_subcategory, "subcategory"), (by_place, "place")):
        template = await _refresh(db_session, template)
        assert template.is_active is False
        assert kind in template.last_error
        await recurring_service.generate_due(
            db_session, template.id, today=TODAY + timedelta(days=2)
        )
        assert await _count(db_session, Income, template.id) == 0


async def test_deleting_a_category_pauses_templates_using_its_subcategories(
    db_session: AsyncSession, test_user: User
):
    category = await create_test_category(db_session, owner_id=test_user.id)
    subcategory = await create_test_subcategory(
        db_session, owner_id=test_user.id, category_id=category.id
    )
    template = await _create(
        db_session,
        test_user,
        category_id=category.id,
        subcategory_id=subcategory.id,
    )
    await catalog_service.delete_category(db_session, test_user, category.id)

    template = await _refresh(db_session, template)
    assert template.is_active is False
    assert template.last_error


async def test_template_without_an_account_keeps_generating(
    db_session: AsyncSession, test_user: User
):
    other = await create_test_account(db_session, owner_id=test_user.id)
    template = await _create(db_session, test_user)
    await account_service.delete_account(db_session, test_user, other.id)

    created = await recurring_service.generate_due(
        db_session, template.id, today=TODAY + timedelta(days=2)
    )
    template = await _refresh(db_session, template)
    assert created == 2
    assert template.is_active is True
    assert template.last_error is None


async def test_foreign_account_pauses_instead_of_generating_unassigned(
    db_session: AsyncSession, test_user: User
):
    stranger = await create_test_user(db_session, email="stranger@example.com")
    foreign = await create_test_account(db_session, owner_id=stranger.id)
    template = await _create(db_session, test_user)
    template.account_id = foreign.id
    await db_session.commit()

    created = await recurring_service.generate_due(
        db_session, template.id, today=TODAY + timedelta(days=2)
    )
    template = await _refresh(db_session, template)
    assert created == 0
    assert template.is_active is False
    assert template.last_error
    assert await _count(db_session, Expense, template.id) == 0


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


def _set_today(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    monkeypatch.setattr(recurring_service, "local_today", lambda tz: day)


async def _create_monthly_on_15th(
    db: AsyncSession, user: User, **fields
) -> RecurringTransaction:
    """Created on 15 Oct 2031, so the first occurrence is already generated."""
    return await _create(
        db,
        user,
        frequency="monthly",
        start_date=date(2031, 10, 15),
        month_days=[15],
        **fields,
    )


async def _update(db: AsyncSession, user: User, template, **fields):
    return await recurring_service.update_recurring(
        db, user, template.id, schemas.RecurringTransactionUpdate(**fields)
    )


async def test_editing_limits_keeps_weekend_occurrence_moved_forward(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    # Saturday 15 Nov 2031 -> Monday 17; the edit happens on Sunday 16.
    _set_today(monkeypatch, date(2031, 10, 15))
    template = await _create_monthly_on_15th(
        db_session, test_user, business_day_adjustment="next"
    )
    assert template.next_run_date == date(2031, 11, 17)

    _set_today(monkeypatch, date(2031, 11, 16))
    template = await _update(
        db_session, test_user, template, end_date=date(2032, 12, 31)
    )
    assert template.next_nominal_date == date(2031, 11, 15)
    assert template.next_run_date == date(2031, 11, 17)


async def test_editing_schedule_keeps_due_occurrence_not_yet_generated(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    _set_today(monkeypatch, date(2031, 10, 15))
    template = await _create_monthly_on_15th(db_session, test_user)
    assert template.occurrences_count == 1

    # The worker hasn't generated the 15 Nov occurrence yet.
    _set_today(monkeypatch, date(2031, 11, 20))
    template = await _update(db_session, test_user, template, max_occurrences=12)
    assert template.occurrences_count == 2
    assert template.last_run_date == date(2031, 11, 15)
    assert template.next_run_date == date(2031, 12, 15)


async def test_editing_limits_keeps_skipped_occurrence_skipped(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    _set_today(monkeypatch, date(2031, 10, 15))
    template = await _create_monthly_on_15th(db_session, test_user)
    _set_today(monkeypatch, date(2031, 11, 2))
    template = await recurring_service.skip_next(db_session, test_user, template.id)
    assert template.next_run_date == date(2031, 12, 15)

    template = await _update(db_session, test_user, template, timezone="UTC")
    assert template.next_run_date == date(2031, 12, 15)


async def test_changing_rule_reschedules_from_today(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    _set_today(monkeypatch, date(2031, 10, 15))
    template = await _create_monthly_on_15th(db_session, test_user)
    _set_today(monkeypatch, date(2031, 11, 2))
    template = await _update(db_session, test_user, template, month_days=[10])
    assert template.next_run_date == date(2031, 11, 10)
    assert template.occurrences_count == 1


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        # Set up on Thursday: the Saturday start moves to Friday 14.
        (date(2031, 11, 13), date(2031, 11, 14)),
        # Set up on Friday 14: Friday has come, so the series starts Saturday.
        (date(2031, 11, 14), date(2031, 11, 15)),
    ],
)
async def test_weekend_start_is_not_generated_on_creation(
    db_session: AsyncSession,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
    today: date,
    expected: date,
):
    monkeypatch.setattr(recurring_service, "local_today", lambda tz: today)
    template = await _create(
        db_session,
        test_user,
        frequency="semimonthly",
        start_date=date(2031, 11, 15),
        business_day_adjustment="previous",
    )
    assert template.next_run_date == expected
    assert template.occurrences_count == 0
    assert await _count(db_session, Expense, template.id) == 0


async def _claimed_batch(
    db: AsyncSession, user: User, monkeypatch: pytest.MonkeyPatch, size: int
) -> list[RecurringTransaction]:
    """Make `size` templates due and route the worker's sessions to the test one."""
    templates = [await _create(db, user) for _ in range(size)]
    for template in templates:
        template.next_run_date = TODAY
    await db.commit()

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(recurring_worker, "async_session", session)
    monkeypatch.setattr(recurring_worker, "BATCH_SIZE", size)
    return templates


async def _locked_until(db: AsyncSession, recurring_id: int) -> datetime | None:
    return await db.scalar(
        select(RecurringTransaction.locked_until).where(
            RecurringTransaction.id == recurring_id
        )
    )


async def test_worker_continues_batch_after_a_template_fails(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    first, failing, last = await _claimed_batch(db_session, test_user, monkeypatch, 3)
    generate_due = recurring_service.generate_due

    async def flaky(db, recurring_id, **kwargs):
        if recurring_id == failing.id:
            raise ConnectionError("transient")
        return await generate_due(db, recurring_id, **kwargs)

    monkeypatch.setattr(recurring_worker, "generate_due", flaky)

    assert await recurring_worker.tick() is True
    assert await _count(db_session, Expense, first.id) == 1
    assert await _count(db_session, Expense, last.id) == 1
    # The failing template keeps its lease and is retried once it expires.
    assert await _count(db_session, Expense, failing.id) == 0
    assert await _locked_until(db_session, failing.id) is not None


async def test_worker_releases_leases_of_unreached_templates(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    interrupted, *unreached = await _claimed_batch(
        db_session, test_user, monkeypatch, 3
    )

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(recurring_worker, "generate_due", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await recurring_worker.tick()
    assert await _locked_until(db_session, interrupted.id) is not None
    for template in unreached:
        assert await _locked_until(db_session, template.id) is None


# -- marking a transaction as recurring -----------------------------------------
def _from_expense(expense_id: int) -> schemas.RecurringFromTransaction:
    return schemas.RecurringFromTransaction(
        transaction_type="expense",
        transaction_id=expense_id,
        frequency="monthly",
        timezone="UTC",
    )


async def test_concurrent_marks_of_one_transaction_create_one_template(
    monkeypatch: pytest.MonkeyPatch,
):
    """Two requests (a double click) both see the transaction unlinked."""
    from tests.conftest import TestSessionLocal

    async with TestSessionLocal() as setup:
        user = User(email=f"race-{uuid4()}@example.com", country="USD")
        setup.add(user)
        await setup.commit()
        expense = Expense(owner_id=user.id, amount=9.99, date=TODAY - timedelta(days=1))
        setup.add(expense)
        await setup.commit()
        user_id, expense_id = user.id, expense.id

    # Hold both requests right after they read the transaction, so each one
    # checks `recurring_id` before either of them links it.
    both_read = asyncio.Barrier(2)
    get_expense = recurring_service.transaction_service.get_expense

    async def get_then_wait(db, owner, transaction_id):
        transaction = await get_expense(db, owner, transaction_id)
        await both_read.wait()
        return transaction

    monkeypatch.setattr(
        recurring_service.transaction_service, "get_expense", get_then_wait
    )

    async def mark():
        async with TestSessionLocal() as db:
            owner = await db.get(User, user_id)
            return await recurring_service.create_from_transaction(
                db, owner, _from_expense(expense_id)
            )

    try:
        results = await asyncio.wait_for(
            asyncio.gather(mark(), mark(), return_exceptions=True), timeout=30
        )
        created = [r for r in results if isinstance(r, RecurringTransaction)]
        conflicts = [r for r in results if isinstance(r, ConflictError)]
        assert (len(created), len(conflicts)) == (1, 1), results

        async with TestSessionLocal() as verify:
            templates = await verify.execute(
                select(RecurringTransaction.id).where(
                    RecurringTransaction.owner_id == user_id
                )
            )
            assert templates.scalars().all() == [created[0].id]
            linked = await verify.get(Expense, expense_id)
            assert linked.recurring_id == created[0].id
    finally:
        async with TestSessionLocal() as cleanup:
            await cleanup.execute(delete(Expense).where(Expense.owner_id == user_id))
            await cleanup.execute(
                delete(RecurringTransaction).where(
                    RecurringTransaction.owner_id == user_id
                )
            )
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()


async def test_mark_as_recurring_conflicts_when_linked_after_the_check(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    """The transaction read as unlinked is linked by another request meanwhile."""
    expense = Expense(
        owner_id=test_user.id, amount=9.99, date=TODAY - timedelta(days=1)
    )
    db_session.add(expense)
    await db_session.commit()
    winner = await _create(db_session, test_user)
    get_expense = recurring_service.transaction_service.get_expense

    async def linked_meanwhile(db, owner, transaction_id):
        transaction = await get_expense(db, owner, transaction_id)
        # Another request links it; this one keeps its stale read.
        await db.execute(
            update(Expense)
            .where(Expense.id == transaction_id)
            .values(recurring_id=winner.id)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return transaction

    monkeypatch.setattr(
        recurring_service.transaction_service, "get_expense", linked_meanwhile
    )

    with pytest.raises(ConflictError):
        await recurring_service.create_from_transaction(
            db_session, test_user, _from_expense(expense.id)
        )

    templates = await db_session.execute(
        select(RecurringTransaction.id).where(
            RecurringTransaction.owner_id == test_user.id
        )
    )
    assert templates.scalars().all() == [winner.id]
    expense = await db_session.get(Expense, expense.id, populate_existing=True)
    assert expense.recurring_id == winner.id
