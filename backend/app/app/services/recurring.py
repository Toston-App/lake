"""Recurring transaction templates: create, update, skip, delete.

A template's schedule bookkeeping lives in `recurring_schedule`, field checks
in `recurring_fields`, its lease in `recurring_leases` and the creation of
its occurrences in `recurring_generation`; the public names of those modules
are re-exported here. User changes (update, skip, delete) take the same lease
as a generation run, so they never interleave with one.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app import crud, models, schemas
from app.core.recurrence import next_occurrence, snap_to_nominal
from app.services.errors import ConflictError, DomainError, NotFoundError
from app.services.ownership import require_owned
from app.services.recurring_fields import MAX_FUTURE_YEARS as MAX_FUTURE_YEARS
from app.services.recurring_fields import MAX_PAST_YEARS as MAX_PAST_YEARS
from app.services.recurring_fields import InvalidTimezoneError as InvalidTimezoneError
from app.services.recurring_fields import _shift_years as _shift_years
from app.services.recurring_fields import (
    _validate_dates,
    _validate_fields,
    _validate_rule,
    _validate_timezone,
)
from app.services.recurring_fields import earliest_start as earliest_start
from app.services.recurring_fields import pause_for_deleted as pause_for_deleted
from app.services.recurring_fields import preview as preview
from app.services.recurring_generation import (
    MAX_OCCURRENCES_PER_RUN as MAX_OCCURRENCES_PER_RUN,
)
from app.services.recurring_generation import claim_due as claim_due
from app.services.recurring_generation import generate_due as generate_due
from app.services.recurring_generation import logger as logger
from app.services.recurring_generation import (
    report_unknown_timezones as report_unknown_timezones,
)
from app.services.recurring_leases import LEASE as LEASE
from app.services.recurring_leases import _acquire_lease, _release_lease
from app.services.recurring_leases import release_leases as release_leases
from app.services.recurring_schedule import UPCOMING_PREVIEW as UPCOMING_PREVIEW
from app.services.recurring_schedule import RecurringTransaction as RecurringTransaction
from app.services.recurring_schedule import _advance, _reschedule, _set_next
from app.services.recurring_schedule import local_today as local_today
from app.services.recurring_schedule import postgres_timezones as postgres_timezones
from app.services.recurring_schedule import rule_for as rule_for
from app.services.recurring_schedule import rule_from as rule_from
from app.services.recurring_schedule import to_detail as to_detail
from app.services.recurring_schedule import upcoming_for as upcoming_for
from app.services.transaction_kinds import KINDS, kind_of

# How long a user change waits for a generation run to release the lease.
LEASE_WAIT_SECONDS = 3.0
LEASE_POLL_SECONDS = 0.2
MAX_TEMPLATES_PER_USER = 500

SCHEDULE_FIELDS = frozenset(
    {
        "frequency",
        "interval",
        "weekdays",
        "monthly_mode",
        "month_days",
        "week_of_month",
        "business_day_adjustment",
        "start_date",
        "end_date",
        "max_occurrences",
        "timezone",
    }
)
# Schedule fields that don't change which dates the rule produces.
LIMIT_FIELDS = frozenset({"end_date", "max_occurrences", "timezone"})


# Updatable columns that are NOT NULL in the database.
NON_NULLABLE_FIELDS = (
    "amount",
    "frequency",
    "interval",
    "weekdays",
    "monthly_mode",
    "month_days",
    "business_day_adjustment",
    "start_date",
    "timezone",
    "is_active",
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
async def get_recurring(
    db: AsyncSession, user: models.User, recurring_id: int
) -> RecurringTransaction:
    template = await db.get(RecurringTransaction, recurring_id, populate_existing=True)
    # Another user's template is reported as missing, so ids can't be probed.
    if not template or (
        not crud.user.is_superuser(user) and template.owner_id != user.id
    ):
        raise NotFoundError("Recurring transaction not found")
    return template


async def list_recurring(
    db: AsyncSession, user: models.User
) -> list[RecurringTransaction]:
    result = await db.execute(
        select(RecurringTransaction)
        .where(RecurringTransaction.owner_id == user.id)
        .order_by(
            RecurringTransaction.is_active.desc(),
            RecurringTransaction.next_run_date.asc().nullslast(),
            RecurringTransaction.id,
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
async def _lease_for_change(
    db: AsyncSession, user: models.User, recurring_id: int
) -> tuple[RecurringTransaction, datetime]:
    """Load a template for a user change while holding its lease.

    Waits briefly for a running generation to finish; returns the template and
    the lease token. The caller must release the lease (or delete the template)
    when done.
    """
    deadline = time.monotonic() + LEASE_WAIT_SECONDS
    while True:
        await get_recurring(db, user, recurring_id)
        lease = await _acquire_lease(db, recurring_id)
        if lease:
            # Reload: a generation run may have changed it while we waited.
            return await get_recurring(db, user, recurring_id), lease
        if time.monotonic() >= deadline:
            raise ConflictError(
                "This recurring transaction is being processed, try again shortly"
            )
        await asyncio.sleep(LEASE_POLL_SECONDS)


async def _abandon_change(db: AsyncSession, recurring_id: int, lease: datetime) -> None:
    await db.rollback()
    await _release_lease(db, recurring_id, lease)


async def create_recurring(
    db: AsyncSession,
    user: models.User,
    data: schemas.RecurringTransactionCreate,
    *,
    source_transaction=None,
    detection_fingerprint: str | None = None,
) -> RecurringTransaction:
    """Create a template and generate anything already due.

    With `source_transaction`, that transaction becomes the first occurrence
    of the series (it is linked in the same commit, so it is never duplicated);
    `ConflictError` if another series linked it since the caller checked.
    With `detection_fingerprint` (a detected series being accepted):
    - the transaction counts as the rule's nearest date (within a few days),
      so a charge on Friday the 13th for a series due on the 15th doesn't
      create another one two days later;
    - dates the series missed before today are skipped instead of back-filled:
      the user may have cancelled, or not imported them yet.
    """
    fields = data.model_dump(exclude_none=True)
    fields.setdefault("timezone", "UTC")
    await _validate_timezone(db, fields["timezone"])
    _validate_dates(fields)
    _validate_rule(rule_from(fields))
    fields = await _validate_fields(db, user, data.type, fields)
    count = await db.scalar(
        select(func.count())
        .select_from(RecurringTransaction)
        .where(RecurringTransaction.owner_id == user.id)
    )
    if count >= MAX_TEMPLATES_PER_USER:
        raise DomainError(
            f"You can have at most {MAX_TEMPLATES_PER_USER} recurring transactions"
        )

    template = RecurringTransaction(
        owner_id=user.id,
        occurrences_count=0,
        detection_fingerprint=detection_fingerprint,
        **fields,
    )
    if source_transaction is not None:
        template.source_transaction_id = source_transaction.id
        template.occurrences_count = 1
        template.last_run_date = data.start_date
        rule = rule_for(template)
        covered = data.start_date
        last_actual = data.start_date
        if detection_fingerprint is not None:
            covered = max(covered, snap_to_nominal(rule, data.start_date) or covered)
            yesterday = local_today(template.timezone) - timedelta(days=1)
            last_actual = max(last_actual, yesterday)
        # The transaction covers the start date itself, even when a weekend
        # adjustment would move that date forward.
        _set_next(
            template,
            next_occurrence(rule, after_nominal=covered, last_actual=last_actual),
        )
    else:
        _reschedule(template)
    # A savepoint, so losing the source transaction to another series undoes
    # only this template and leaves the caller's session usable.
    async with db.begin_nested():
        db.add(template)
        await db.flush()
        if source_transaction is not None:
            await _link_source(db, user, source_transaction, template.id)
    await db.commit()
    await generate_due(db, template.id)
    return await get_recurring(db, user, template.id)


async def _link_source(
    db: AsyncSession, user: models.User, transaction, recurring_id: int
) -> None:
    """Make `transaction` the first occurrence, unless a series already has it.

    The caller checked `recurring_id` on a row it read earlier; a concurrent
    request (a double click, or accepting the same transaction as a suggestion)
    may have linked it since. The conditional update waits for that request's
    row lock and then matches nothing, so only one template ever gets the
    transaction.
    """
    model = type(transaction)
    result = await db.execute(
        update(model)
        .where(
            model.id == transaction.id,
            model.owner_id == user.id,
            model.recurring_id.is_(None),
        )
        .values(recurring_id=recurring_id)
        .returning(model.id)
        .execution_options(synchronize_session=False)
    )
    if result.first() is None:
        raise ConflictError("This transaction already belongs to a recurring series")
    set_committed_value(transaction, "recurring_id", recurring_id)


async def update_recurring(
    db: AsyncSession,
    user: models.User,
    recurring_id: int,
    data: schemas.RecurringTransactionUpdate,
) -> RecurringTransaction:
    changes = data.model_dump(exclude_unset=True)
    for required in NON_NULLABLE_FIELDS:
        if required in changes and changes[required] is None:
            raise DomainError(f"{required} cannot be empty")
    template, lease = await _lease_for_change(db, user, recurring_id)
    try:
        await _apply_update(db, user, template, changes)
    except Exception:
        await _abandon_change(db, recurring_id, lease)
        raise
    # Still leased: generate what the change made due, then release.
    await generate_due(db, template.id, lease=lease)
    return await get_recurring(db, user, template.id)


async def _apply_update(
    db: AsyncSession,
    user: models.User,
    template: RecurringTransaction,
    changes: dict,
) -> None:
    if changes.get("timezone") not in (None, template.timezone):
        await _validate_timezone(db, changes["timezone"])
    merged = {
        column: getattr(template, column)
        for column in (
            "account_id",
            "to_account_id",
            "category_id",
            "subcategory_id",
            "place_id",
            *SCHEDULE_FIELDS,
        )
    }
    merged.update(changes)
    # Only the dates being changed: an older series keeps its start date, even
    # when a client sends it back unchanged with the rest of the form.
    _validate_dates(
        merged,
        keys=[k for k, v in changes.items() if v != getattr(template, k, None)],
    )
    _validate_rule(rule_from(merged))
    merged = await _validate_fields(db, user, template.type, merged)
    changes.update(
        {
            k: merged[k]
            for k in ("to_account_id", "category_id", "subcategory_id", "place_id")
        }
    )

    resumed = changes.get("is_active") is True and not template.is_active
    for key, value in changes.items():
        setattr(template, key, value)

    if resumed:
        # Resuming skips what was missed while paused instead of back-filling it.
        template.last_error = None
        _reschedule(template, from_date=local_today(template.timezone))
    elif SCHEDULE_FIELDS & changes.keys():
        # Only future occurrences follow the new schedule. The pending one is
        # kept even when its nominal date has passed (a weekend date moved
        # forward, or a due date the worker hasn't reached yet).
        from_date = local_today(template.timezone) if template.last_run_date else None
        if template.next_nominal_date:
            if not (changes.keys() & SCHEDULE_FIELDS) - LIMIT_FIELDS:
                # The rule's dates are unchanged: keep the pending occurrence.
                from_date = template.next_nominal_date
            elif from_date:
                from_date = min(from_date, template.next_nominal_date)
        _reschedule(template, from_date=from_date)

    await db.commit()


async def skip_next(
    db: AsyncSession, user: models.User, recurring_id: int
) -> RecurringTransaction:
    template, lease = await _lease_for_change(db, user, recurring_id)
    if not template.next_run_date:
        await _abandon_change(db, recurring_id, lease)
        raise DomainError("This recurring transaction has no upcoming date")
    _advance(template)
    template.locked_until = None
    await db.commit()
    return await get_recurring(db, user, template.id)


async def delete_recurring(
    db: AsyncSession,
    user: models.User,
    recurring_id: int,
    *,
    delete_generated: bool = False,
) -> RecurringTransaction:
    template, lease = await _lease_for_change(db, user, recurring_id)
    try:
        await _delete_template(db, template, delete_generated=delete_generated)
    except Exception:
        await _abandon_change(db, recurring_id, lease)
        raise
    return template


async def _delete_template(
    db: AsyncSession, template: RecurringTransaction, *, delete_generated: bool
) -> None:
    if delete_generated:
        owner = await crud.user.get(db, id=template.owner_id)
        kind = kind_of(template.type)
        model = kind.model
        ids = (
            (
                await db.execute(
                    select(model.id).where(
                        model.recurring_id == template.id,
                        # The transaction marked as recurring is not generated.
                        model.id.is_distinct_from(template.source_transaction_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        for transaction_id in ids:
            await kind.service("delete")(db, owner, transaction_id)
    await db.delete(template)
    await db.commit()


async def create_from_transaction(
    db: AsyncSession,
    user: models.User,
    data: schemas.RecurringFromTransaction,
    *,
    detection_fingerprint: str | None = None,
) -> RecurringTransaction:
    kind = KINDS[data.transaction_type]
    # Another user's transaction is reported as missing, so ids can't be
    # probed; not even a superuser can make a series of it.
    transaction = await require_owned(
        db,
        user,
        kind.crud,
        data.transaction_id,
        "Transaction",
        hide_foreign=True,
        allow_superuser=False,
    )
    if transaction.recurring_id:
        raise DomainError("This transaction already belongs to a recurring series")
    tz = data.timezone or "UTC"
    if transaction.date and transaction.date < earliest_start(local_today(tz)):
        raise DomainError(
            f"Only transactions from the last {MAX_PAST_YEARS} years can be "
            "marked as recurring"
        )

    copied = kind.template_fields(transaction)
    copied.update(amount=transaction.amount, description=transaction.description)
    template_fields = set(schemas.TemplateFields.model_fields)
    copied.update(data.model_dump(exclude_unset=True, include=template_fields))
    start_date = transaction.date or local_today(data.timezone or "UTC")
    create_in = schemas.RecurringTransactionCreate(
        **data.model_dump(
            exclude_none=True,
            exclude=template_fields
            | {"transaction_type", "transaction_id", "fingerprint"},
        ),
        **{k: v for k, v in copied.items() if v is not None},
        type=kind.type,
        start_date=start_date,
    )
    return await create_recurring(
        db,
        user,
        create_in,
        source_transaction=transaction,
        detection_fingerprint=detection_fingerprint,
    )
