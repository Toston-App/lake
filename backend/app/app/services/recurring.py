"""Recurring transaction templates and the generation of their occurrences.

Generated transactions go through the regular transaction services so that
account, category and user totals stay consistent. Because those services
commit several times per transaction, a template is protected with a short
lease (`locked_until`) instead of a row lock while it is being generated. The
exact `locked_until` value works as the lease token: it is renewed with a
compare-and-set before every occurrence, so a holder whose lease was taken over
after expiring stops instead of generating alongside the new holder.
User changes (update, skip, delete) take the same lease, so they never
interleave with a generation run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, cast, exists, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app import crud, models, schemas
from app.core.recurrence import (
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    Occurrence,
    RecurringType,
    Rule,
    next_occurrence,
    snap_to_nominal,
    upcoming,
)
from app.schemas.transaction import TransactionType
from app.services import transactions as transaction_service
from app.services.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    ServiceError,
)

logger = logging.getLogger(__name__)

RecurringTransaction = models.RecurringTransaction

MADE_FROM = "Recurring"
LEASE = timedelta(minutes=5)
# How long a user change waits for a generation run to release the lease.
LEASE_WAIT_SECONDS = 3.0
LEASE_POLL_SECONDS = 0.2
# Missed occurrences created per template in one run; the rest wait for the next run.
MAX_OCCURRENCES_PER_RUN = 60
UPCOMING_PREVIEW = 5
# A series may start at most this far back: it back-fills from its start date.
MAX_PAST_YEARS = 2
# Start and end dates further ahead than this are rejected.
MAX_FUTURE_YEARS = 10
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
TRANSACTION_MODELS = {
    RecurringType.expense: models.Expense,
    RecurringType.income: models.Income,
    RecurringType.transfer: models.Transfer,
}


def local_today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def _shift_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # Feb 29 in a non-leap year
        return day.replace(year=day.year + years, day=28)


def earliest_start(today: date) -> date:
    return _shift_years(today, -MAX_PAST_YEARS)


def _validate_dates(fields: dict, keys=("start_date", "end_date")) -> None:
    """Keep start and end dates within MAX_PAST_YEARS / MAX_FUTURE_YEARS of today."""
    today = local_today(fields.get("timezone") or "UTC")
    latest = _shift_years(today, MAX_FUTURE_YEARS)
    start = fields.get("start_date")
    if "start_date" in keys and start is not None:
        if start < earliest_start(today):
            raise DomainError(
                f"start_date can't be more than {MAX_PAST_YEARS} years in the past"
            )
        if start > latest:
            raise DomainError(
                f"start_date can't be more than {MAX_FUTURE_YEARS} years ahead"
            )
    end = fields.get("end_date")
    if "end_date" in keys and end is not None and end > latest:
        raise DomainError(f"end_date can't be more than {MAX_FUTURE_YEARS} years ahead")


def rule_for(template: RecurringTransaction) -> Rule:
    return Rule(
        frequency=Frequency(template.frequency),
        start_date=template.start_date,
        interval=template.interval or 1,
        weekdays=tuple(template.weekdays or ()),
        monthly_mode=MonthlyMode(template.monthly_mode or MonthlyMode.day_of_month),
        month_days=tuple(template.month_days or ()),
        week_of_month=template.week_of_month,
        adjustment=BusinessDayAdjustment(
            template.business_day_adjustment or BusinessDayAdjustment.none
        ),
        end_date=template.end_date,
    )


def _limit_reached(template: RecurringTransaction) -> bool:
    return bool(
        template.max_occurrences
        and template.occurrences_count >= template.max_occurrences
    )


def _set_next(template: RecurringTransaction, occurrence: Occurrence | None) -> None:
    if occurrence is None or _limit_reached(template):
        template.next_nominal_date = None
        template.next_run_date = None
        return
    template.next_nominal_date = occurrence.nominal
    template.next_run_date = occurrence.actual


def _reschedule(
    template: RecurringTransaction, *, from_date: date | None = None
) -> None:
    """Recompute the next occurrence from scratch, after the last generated one."""
    _set_next(
        template,
        next_occurrence(
            rule_for(template),
            last_actual=template.last_run_date,
            from_date=from_date,
            today=local_today(template.timezone),
        ),
    )


def _advance(template: RecurringTransaction) -> None:
    _set_next(
        template,
        next_occurrence(
            rule_for(template),
            after_nominal=template.next_nominal_date,
            last_actual=max(
                d for d in (template.last_run_date, template.next_run_date) if d
            ),
        ),
    )


def upcoming_for(
    template: RecurringTransaction, count: int = UPCOMING_PREVIEW
) -> list[Occurrence]:
    if not template.next_run_date:
        return []
    if template.max_occurrences:
        count = min(count, template.max_occurrences - template.occurrences_count)
    if count <= 0:
        return []
    first = Occurrence(
        nominal=template.next_nominal_date, actual=template.next_run_date
    )
    return [first] + upcoming(
        rule_for(template),
        count - 1,
        after_nominal=first.nominal,
        last_actual=first.actual,
    )


def to_detail(template: RecurringTransaction) -> schemas.RecurringTransactionDetail:
    detail = schemas.RecurringTransactionDetail.model_validate(template)
    detail.upcoming = [
        schemas.UpcomingOccurrence(nominal_date=o.nominal, date=o.actual)
        for o in upcoming_for(template)
    ]
    return detail


def preview(data: schemas.RecurringPreviewRequest) -> schemas.RecurringPreview:
    fields = data.model_dump()
    _validate_dates(fields)
    rule = _rule_from_fields(fields)
    _validate_rule(rule)
    count = min(UPCOMING_PREVIEW, data.max_occurrences or UPCOMING_PREVIEW)
    after = data.start_date if data.exclude_start else None
    if after and data.max_occurrences:
        count = min(count, data.max_occurrences - 1)
    return schemas.RecurringPreview(
        upcoming=[
            schemas.UpcomingOccurrence(nominal_date=o.nominal, date=o.actual)
            for o in upcoming(
                rule,
                count,
                after_nominal=after,
                last_actual=after,
                today=local_today(data.timezone or "UTC"),
            )
        ]
    )


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


def _rule_from_fields(fields: dict) -> Rule:
    return Rule(
        frequency=fields["frequency"],
        start_date=fields["start_date"],
        interval=fields.get("interval") or 1,
        weekdays=tuple(fields.get("weekdays") or ()),
        monthly_mode=fields.get("monthly_mode") or MonthlyMode.day_of_month,
        month_days=tuple(fields.get("month_days") or ()),
        week_of_month=fields.get("week_of_month"),
        adjustment=fields.get("business_day_adjustment") or BusinessDayAdjustment.none,
        end_date=fields.get("end_date"),
    )


def _validate_rule(rule: Rule) -> None:
    if rule.end_date and rule.end_date < rule.start_date:
        raise DomainError("end_date must be on or after start_date")
    if rule.frequency == Frequency.semimonthly and rule.month_days:
        if len(rule.month_days) != 2:
            raise DomainError("Twice-a-month schedules need exactly two month_days")
    if (
        rule.frequency == Frequency.monthly
        and rule.monthly_mode == MonthlyMode.nth_weekday
    ):
        if rule.week_of_month is None:
            raise DomainError("nth_weekday schedules need week_of_month")
        if len(rule.weekdays) > 1:
            raise DomainError("nth_weekday schedules take a single weekday")
    if next_occurrence(rule) is None:
        raise DomainError("This schedule never produces a date")


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


async def _require_ref(
    db: AsyncSession, user: models.User, crud_obj, obj_id: int | None, label: str
):
    if obj_id is None:
        return None
    obj = await crud_obj.get(db=db, id=obj_id)
    if not obj or (not crud.user.is_superuser(user) and obj.owner_id != user.id):
        raise NotFoundError(f"{label} not found")
    return obj


async def _validate_fields(
    db: AsyncSession, user: models.User, recurring_type: RecurringType, fields: dict
) -> dict:
    """Check ownership and per-type rules; drop fields that don't apply to the type."""
    await _require_ref(db, user, crud.account, fields.get("account_id"), "Account")
    if recurring_type == RecurringType.transfer:
        await _require_ref(
            db, user, crud.account, fields.get("to_account_id"), "Account"
        )
        if not fields.get("account_id") or not fields.get("to_account_id"):
            raise DomainError("Transfers need account_id and to_account_id")
        if fields["account_id"] == fields["to_account_id"]:
            raise DomainError("Transfers need two different accounts")
        fields.update(category_id=None, subcategory_id=None, place_id=None)
        return fields

    fields["to_account_id"] = None
    await _require_ref(db, user, crud.place, fields.get("place_id"), "Place")
    subcategory = await _require_ref(
        db, user, crud.subcategory, fields.get("subcategory_id"), "Subcategory"
    )
    if recurring_type == RecurringType.income:
        # Incomes are categorised through their subcategory only.
        fields["category_id"] = None
        return fields

    await _require_ref(db, user, crud.category, fields.get("category_id"), "Category")
    if subcategory and subcategory.category_id != fields.get("category_id"):
        raise DomainError("Subcategory does not belong to the category")
    return fields


async def create_recurring(
    db: AsyncSession,
    user: models.User,
    data: schemas.RecurringTransactionCreate,
    *,
    source_transaction=None,
    detection_fingerprint: str | None = None,
    from_suggestion: bool = False,
) -> RecurringTransaction:
    """Create a template and generate anything already due.

    With `source_transaction`, that transaction becomes the first occurrence
    of the series (it is linked in the same commit, so it is never duplicated);
    `ConflictError` if another series linked it since the caller checked.
    With `from_suggestion` (a detected series being accepted):
    - the transaction counts as the rule's nearest date (within a few days),
      so a charge on Friday the 13th for a series due on the 15th doesn't
      create another one two days later;
    - dates the series missed before today are skipped instead of back-filled:
      the user may have cancelled, or not imported them yet.
    """
    fields = data.model_dump(exclude_none=True)
    fields.setdefault("timezone", "UTC")
    _validate_dates(fields)
    _validate_rule(_rule_from_fields(fields))
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
        if from_suggestion:
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
    _validate_rule(_rule_from_fields(merged))
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
        model = TRANSACTION_MODELS[RecurringType(template.type)]
        delete = {
            RecurringType.expense: transaction_service.delete_expense,
            RecurringType.income: transaction_service.delete_income,
            RecurringType.transfer: transaction_service.delete_transfer,
        }[RecurringType(template.type)]
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
            await delete(db, owner, transaction_id)
    await db.delete(template)
    await db.commit()


# Template columns that point at each kind of referenced entity.
_REFERENCE_COLUMNS = {
    "account": ("account_id", "to_account_id"),
    "category": ("category_id",),
    "subcategory": ("subcategory_id",),
    "place": ("place_id",),
}


async def pause_for_deleted(db: AsyncSession, kind: str, ids: list[int]) -> None:
    """Pause the templates that use an entity which is about to be deleted.

    The template foreign keys are ON DELETE SET NULL, so once the entity is gone
    a template that lost it looks like one that never had it and would keep
    generating unassigned transactions. Call this before the delete, in the same
    transaction.
    """
    if not ids:
        return
    columns = [getattr(RecurringTransaction, c) for c in _REFERENCE_COLUMNS[kind]]
    await db.execute(
        update(RecurringTransaction)
        .where(or_(*(column.in_(ids) for column in columns)))
        .values(
            is_active=False,
            last_error=f"The {kind} used by this recurring transaction was deleted",
        )
        .execution_options(synchronize_session=False)
    )


async def create_from_transaction(
    db: AsyncSession,
    user: models.User,
    data: schemas.RecurringFromTransaction,
    *,
    detection_fingerprint: str | None = None,
) -> RecurringTransaction:
    getter = {
        TransactionType.expense: transaction_service.get_expense,
        TransactionType.income: transaction_service.get_income,
        TransactionType.transfer: transaction_service.get_transfer,
    }[data.transaction_type]
    try:
        transaction = await getter(db, user, data.transaction_id)
    except PermissionDeniedError:
        transaction = None
    # Another user's transaction is reported as missing, so ids can't be probed.
    if transaction is None or transaction.owner_id != user.id:
        raise NotFoundError("Transaction not found")
    if transaction.recurring_id:
        raise DomainError("This transaction already belongs to a recurring series")
    tz = data.timezone or "UTC"
    if transaction.date and transaction.date < earliest_start(local_today(tz)):
        raise DomainError(
            f"Only transactions from the last {MAX_PAST_YEARS} years can be "
            "marked as recurring"
        )

    recurring_type = RecurringType(data.transaction_type.value)
    if recurring_type == RecurringType.transfer:
        copied = {
            "account_id": transaction.from_acc,
            "to_account_id": transaction.to_acc,
        }
    else:
        copied = {
            "account_id": transaction.account_id,
            "subcategory_id": transaction.subcategory_id,
            "place_id": transaction.place_id,
            "category_id": getattr(transaction, "category_id", None),
        }
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
        type=recurring_type,
        start_date=start_date,
    )
    return await create_recurring(
        db,
        user,
        create_in,
        source_transaction=transaction,
        detection_fingerprint=detection_fingerprint,
        from_suggestion=detection_fingerprint is not None,
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
async def _acquire_lease(db: AsyncSession, recurring_id: int) -> datetime | None:
    """Lease a free template; returns the lease token, or None if it is held."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(RecurringTransaction)
        .where(
            RecurringTransaction.id == recurring_id,
            or_(
                RecurringTransaction.locked_until.is_(None),
                RecurringTransaction.locked_until < now,
            ),
        )
        .values(locked_until=now + LEASE)
        .returning(RecurringTransaction.id)
        .execution_options(synchronize_session=False)
    )
    acquired = result.first() is not None
    await db.commit()
    return now + LEASE if acquired else None


async def _renew_lease(
    db: AsyncSession, recurring_id: int, lease: datetime
) -> datetime | None:
    """Extend a lease we hold; returns the new token, or None if it was lost.

    An expired lease is still ours as long as nobody else acquired it since.
    """
    renewed = datetime.now(timezone.utc) + LEASE
    result = await db.execute(
        update(RecurringTransaction)
        .where(
            RecurringTransaction.id == recurring_id,
            RecurringTransaction.locked_until == lease,
        )
        .values(locked_until=renewed)
        .returning(RecurringTransaction.id)
        .execution_options(synchronize_session=False)
    )
    held = result.first() is not None
    await db.commit()
    return renewed if held else None


async def _release_lease(db: AsyncSession, recurring_id: int, lease: datetime) -> None:
    await db.execute(
        update(RecurringTransaction)
        .where(
            RecurringTransaction.id == recurring_id,
            RecurringTransaction.locked_until == lease,
        )
        .values(locked_until=None)
        .execution_options(synchronize_session=False)
    )
    await db.commit()


async def claim_due(db: AsyncSession, *, limit: int = 50) -> dict[int, datetime]:
    """Lease up to `limit` templates that have an occurrence due today or earlier.

    Returns the lease token of each claimed template, to pass to `generate_due`.
    """
    now = datetime.now(timezone.utc)
    local_date = cast(func.timezone(RecurringTransaction.timezone, func.now()), Date)
    due = (
        select(RecurringTransaction.id)
        .where(
            RecurringTransaction.is_active.is_(True),
            RecurringTransaction.next_run_date.is_not(None),
            RecurringTransaction.next_run_date <= local_date,
            or_(
                RecurringTransaction.locked_until.is_(None),
                RecurringTransaction.locked_until < now,
            ),
        )
        .order_by(RecurringTransaction.next_run_date)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(
        update(RecurringTransaction)
        .where(RecurringTransaction.id.in_(due.scalar_subquery()))
        .values(locked_until=now + LEASE)
        .returning(RecurringTransaction.id)
        .execution_options(synchronize_session=False)
    )
    leases = {row.id: now + LEASE for row in result}
    await db.commit()
    return leases


async def release_leases(db: AsyncSession, leases: dict[int, datetime]) -> None:
    """Clear the lease of templates that were claimed but will not be generated.

    A lease is only cleared while it still holds our token, so one another
    process took over after it expired is left alone.
    """
    if not leases:
        return
    await db.execute(
        update(RecurringTransaction)
        .where(
            tuple_(RecurringTransaction.id, RecurringTransaction.locked_until).in_(
                list(leases.items())
            )
        )
        .values(locked_until=None)
        .execution_options(synchronize_session=False)
    )
    await db.commit()


async def _already_generated(
    db: AsyncSession, template: RecurringTransaction, run_date: date
) -> bool:
    model = TRANSACTION_MODELS[RecurringType(template.type)]
    return bool(
        await db.scalar(
            select(
                exists().where(
                    and_(model.recurring_id == template.id, model.date == run_date)
                )
            )
        )
    )


async def _create_occurrence(
    db: AsyncSession, owner: models.User, template: RecurringTransaction, run_date: date
) -> None:
    recurring_type = RecurringType(template.type)
    if recurring_type == RecurringType.transfer:
        if not template.account_id or not template.to_account_id:
            raise DomainError("One of the transfer accounts was deleted")
        await transaction_service.create_transfer(
            db,
            owner,
            schemas.TransferCreate(
                amount=template.amount,
                description=template.description,
                date=run_date,
                from_acc=template.account_id,
                to_acc=template.to_account_id,
            ),
            recurring_id=template.id,
        )
        return
    # The transaction services silently drop a missing or foreign reference;
    # a template should pause instead, like transfers do.
    await _require_ref(db, owner, crud.account, template.account_id, "Account")
    await _require_ref(
        db, owner, crud.subcategory, template.subcategory_id, "Subcategory"
    )
    await _require_ref(db, owner, crud.place, template.place_id, "Place")
    if recurring_type == RecurringType.expense:
        await _require_ref(db, owner, crud.category, template.category_id, "Category")
    common = {
        "amount": template.amount,
        "description": template.description,
        "date": run_date.isoformat(),
        "account_id": template.account_id,
        "subcategory_id": template.subcategory_id,
        "place_id": template.place_id,
        "made_from": MADE_FROM,
    }
    if recurring_type == RecurringType.expense:
        await transaction_service.create_expense(
            db,
            owner,
            schemas.ExpenseCreate(**common, category_id=template.category_id),
            recurring_id=template.id,
        )
    else:
        await transaction_service.create_income(
            db, owner, schemas.IncomeCreate(**common), recurring_id=template.id
        )


async def generate_due(
    db: AsyncSession,
    recurring_id: int,
    *,
    today: date | None = None,
    lease: datetime | None = None,
    limit: int = MAX_OCCURRENCES_PER_RUN,
) -> int:
    """Create every occurrence of a template that is due; returns how many were created.

    Callers that already hold the lease (the worker) pass its token as `lease`.
    """
    if lease is None:
        lease = await _acquire_lease(db, recurring_id)
        if lease is None:
            return 0
    template = await db.get(RecurringTransaction, recurring_id, populate_existing=True)
    if template is None:
        return 0

    created = 0
    try:
        owner = await crud.user.get(db, id=template.owner_id)
        today = today or local_today(template.timezone)
        while (
            template.is_active
            and template.next_run_date
            and template.next_run_date <= today
            and created < limit
        ):
            lease = await _renew_lease(db, recurring_id, lease)
            if lease is None:
                # Someone else took the template over after our lease expired.
                logger.warning(
                    "Lost the lease on recurring transaction %s", recurring_id
                )
                return created
            run_date = template.next_run_date
            is_new = not await _already_generated(db, template, run_date)
            if is_new:
                await _create_occurrence(db, owner, template, run_date)
            template.occurrences_count += 1
            template.last_run_date = run_date
            template.last_error = None
            _advance(template)
            await db.commit()
            created += is_new
    except ServiceError as exc:
        # Validation failures happen before anything is written. Pause instead
        # of retrying forever; the user sees why in the UI.
        template.is_active = False
        template.last_error = exc.message
        logger.warning("Paused recurring transaction %s: %s", recurring_id, exc.message)
    except Exception:
        # Unexpected failure: keep the lease so the template is retried once it expires.
        logger.exception("Failed generating recurring transaction %s", recurring_id)
        await db.rollback()
        return created

    await db.commit()
    await _release_lease(db, recurring_id, lease)
    return created
