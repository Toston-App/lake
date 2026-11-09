"""Generation of recurring occurrences: what the worker claims and creates.

Generated transactions go through the regular transaction services so that
account, category and user totals stay consistent. A template is generated
while holding its lease (see `recurring_leases`).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import Date, and_, case, cast, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.services.errors import DomainError, ServiceError
from app.services.recurring_fields import _check_reference
from app.services.recurring_leases import (
    LEASE,
    _acquire_lease,
    _release_lease,
    _renew_lease,
    release_leases,
)
from app.services.recurring_schedule import _advance, local_today, postgres_timezones
from app.services.transaction_kinds import kind_of

__all__ = [
    "MAX_OCCURRENCES_PER_RUN",
    "claim_due",
    "generate_due",
    "release_leases",
    "report_unknown_timezones",
]

# Named after the module generation lived in before, so log filters keep matching.
logger = logging.getLogger("app.services.recurring")

RecurringTransaction = models.RecurringTransaction

# Missed occurrences created per template in one run; the rest wait for the next run.
MAX_OCCURRENCES_PER_RUN = 60

# Templates already reported as skipped for an unknown timezone.
_reported_unknown_timezones: set[int] = set()


async def claim_due(db: AsyncSession, *, limit: int = 50) -> dict[int, datetime]:
    """Lease up to `limit` templates that have an occurrence due today or earlier.

    Returns the lease token of each claimed template, to pass to `generate_due`,
    in the order to generate them: oldest due date first, then by id.
    """
    now = datetime.now(timezone.utc)
    known = sorted(await postgres_timezones(db))
    # A timezone the database doesn't know would fail the whole query, and so
    # stop generation for every user: such templates are skipped instead.
    local_date = case(
        (
            RecurringTransaction.timezone.in_(known),
            cast(func.timezone(RecurringTransaction.timezone, func.now()), Date),
        )
    )
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
        .order_by(RecurringTransaction.next_run_date, RecurringTransaction.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(
        update(RecurringTransaction)
        .where(RecurringTransaction.id.in_(due.scalar_subquery()))
        .values(locked_until=now + LEASE)
        .returning(RecurringTransaction.id, RecurringTransaction.next_run_date)
        .execution_options(synchronize_session=False)
    )
    # RETURNING follows storage order, not the subquery's ORDER BY.
    rows = sorted(result, key=lambda row: (row.next_run_date, row.id))
    leases = {row.id: now + LEASE for row in rows}
    await db.commit()
    return leases


async def report_unknown_timezones(db: AsyncSession) -> list[int]:
    """Warn about active templates `claim_due` skips for their timezone.

    Each template is reported once per process; returns the newly reported ids.
    """
    known = sorted(await postgres_timezones(db))
    result = await db.execute(
        select(RecurringTransaction.id)
        .where(
            RecurringTransaction.is_active.is_(True),
            RecurringTransaction.next_run_date.is_not(None),
            RecurringTransaction.timezone.not_in(known),
        )
        .order_by(RecurringTransaction.id)
    )
    new = [id_ for id_ in result.scalars() if id_ not in _reported_unknown_timezones]
    if new:
        _reported_unknown_timezones.update(new)
        logger.warning(
            "Recurring transactions %s are not generated: the database "
            "doesn't know their timezone",
            new,
        )
    return new


async def _already_generated(
    db: AsyncSession, template: RecurringTransaction, run_date: date
) -> bool:
    model = kind_of(template.type).model
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
    kind = kind_of(template.type)
    if not all(getattr(template, field) for field in kind.required):
        raise DomainError(kind.missing_required)
    for field in kind.checked_references:
        await _check_reference(db, owner, field, getattr(template, field))
    await kind.service("create")(
        db,
        owner,
        kind.build_occurrence(kind.occurrence_fields(template), run_date),
        recurring_id=template.id,
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
