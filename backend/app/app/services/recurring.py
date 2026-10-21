"""Recurring transaction templates and the generation of their occurrences.

Generated transactions go through the regular transaction services so that
account, category and user totals stay consistent. Because those services
commit several times per transaction, a template is protected with a short
lease (`locked_until`) instead of a row lock while it is being generated.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, cast, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.core.recurrence import (
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    Occurrence,
    RecurringType,
    Rule,
    next_occurrence,
    upcoming,
)
from app.schemas.transaction import TransactionType
from app.services import transactions as transaction_service
from app.services.errors import (
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    ServiceError,
)

logger = logging.getLogger(__name__)

RecurringTransaction = models.RecurringTransaction

MADE_FROM = "Recurring"
LEASE = timedelta(minutes=5)
# Missed occurrences created per template in one run; the rest wait for the next run.
MAX_OCCURRENCES_PER_RUN = 60
UPCOMING_PREVIEW = 5

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
TRANSACTION_MODELS = {
    RecurringType.expense: models.Expense,
    RecurringType.income: models.Income,
    RecurringType.transfer: models.Transfer,
}


def local_today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


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
            rule_for(template), last_actual=template.last_run_date, from_date=from_date
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
    rule = _rule_from_fields(data.model_dump())
    _validate_rule(rule)
    count = min(UPCOMING_PREVIEW, data.max_occurrences or UPCOMING_PREVIEW)
    return schemas.RecurringPreview(
        upcoming=[
            schemas.UpcomingOccurrence(nominal_date=o.nominal, date=o.actual)
            for o in upcoming(rule, count)
        ]
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
    if not template:
        raise NotFoundError("Recurring transaction not found")
    if not crud.user.is_superuser(user) and template.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
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
) -> RecurringTransaction:
    """Create a template and generate anything already due.

    With `source_transaction`, that transaction becomes the first occurrence
    of the series (it is linked in the same commit, so it is never duplicated).
    """
    fields = data.model_dump(exclude_none=True)
    fields.setdefault("timezone", "UTC")
    _validate_rule(_rule_from_fields(fields))
    fields = await _validate_fields(db, user, data.type, fields)

    template = RecurringTransaction(owner_id=user.id, occurrences_count=0, **fields)
    if source_transaction is not None:
        template.occurrences_count = 1
        template.last_run_date = data.start_date
    _reschedule(template)
    db.add(template)
    await db.flush()
    if source_transaction is not None:
        source_transaction.recurring_id = template.id
    await db.commit()
    await generate_due(db, template.id)
    return await get_recurring(db, user, template.id)


async def update_recurring(
    db: AsyncSession,
    user: models.User,
    recurring_id: int,
    data: schemas.RecurringTransactionUpdate,
) -> RecurringTransaction:
    template = await get_recurring(db, user, recurring_id)
    changes = data.model_dump(exclude_unset=True)
    for required in ("amount", "frequency", "start_date", "timezone"):
        if required in changes and changes[required] is None:
            raise DomainError(f"{required} cannot be empty")

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
        # Only future occurrences follow the new schedule.
        from_date = local_today(template.timezone) if template.last_run_date else None
        _reschedule(template, from_date=from_date)

    await db.commit()
    await generate_due(db, template.id)
    return await get_recurring(db, user, template.id)


async def skip_next(
    db: AsyncSession, user: models.User, recurring_id: int
) -> RecurringTransaction:
    template = await get_recurring(db, user, recurring_id)
    if not template.next_run_date:
        raise DomainError("This recurring transaction has no upcoming date")
    _advance(template)
    await db.commit()
    return await get_recurring(db, user, template.id)


async def delete_recurring(
    db: AsyncSession,
    user: models.User,
    recurring_id: int,
    *,
    delete_generated: bool = False,
) -> RecurringTransaction:
    template = await get_recurring(db, user, recurring_id)
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
                    select(model.id).where(model.recurring_id == template.id)
                )
            )
            .scalars()
            .all()
        )
        for transaction_id in ids:
            await delete(db, owner, transaction_id)
    await db.delete(template)
    await db.commit()
    return template


async def create_from_transaction(
    db: AsyncSession, user: models.User, data: schemas.RecurringFromTransaction
) -> RecurringTransaction:
    getter = {
        TransactionType.expense: transaction_service.get_expense,
        TransactionType.income: transaction_service.get_income,
        TransactionType.transfer: transaction_service.get_transfer,
    }[data.transaction_type]
    transaction = await getter(db, user, data.transaction_id)
    if transaction.recurring_id:
        raise DomainError("This transaction already belongs to a recurring series")
    if transaction.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")

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
    start_date = transaction.date or local_today(data.timezone or "UTC")
    create_in = schemas.RecurringTransactionCreate(
        **data.model_dump(
            exclude_none=True, exclude={"transaction_type", "transaction_id"}
        ),
        **{k: v for k, v in copied.items() if v is not None},
        type=recurring_type,
        amount=transaction.amount,
        description=transaction.description,
        start_date=start_date,
    )
    return await create_recurring(db, user, create_in, source_transaction=transaction)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
async def _acquire_lease(db: AsyncSession, recurring_id: int) -> bool:
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
    return acquired


async def claim_due(db: AsyncSession, *, limit: int = 50) -> list[int]:
    """Lease up to `limit` templates that have an occurrence due today or earlier."""
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
    ids = [row.id for row in result]
    await db.commit()
    return ids


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
    leased: bool = False,
    limit: int = MAX_OCCURRENCES_PER_RUN,
) -> int:
    """Create every occurrence of a template that is due; returns how many were created.

    Callers that already hold the lease (the worker) pass `leased=True`.
    """
    if not leased and not await _acquire_lease(db, recurring_id):
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
            run_date = template.next_run_date
            if not await _already_generated(db, template, run_date):
                await _create_occurrence(db, owner, template, run_date)
            template.occurrences_count += 1
            template.last_run_date = run_date
            template.last_error = None
            _advance(template)
            await db.commit()
            created += 1
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

    template.locked_until = None
    await db.commit()
    return created
