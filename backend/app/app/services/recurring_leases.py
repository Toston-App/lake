"""Leases that keep generation runs and user changes of a template apart.

Generated transactions go through the regular transaction services, which
commit several times per transaction, so a template is protected with a short
lease (`locked_until`) instead of a row lock while it is being generated. The
exact `locked_until` value works as the lease token: it is renewed with a
compare-and-set before every occurrence, so a holder whose lease was taken over
after expiring stops instead of generating alongside the new holder.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models

RecurringTransaction = models.RecurringTransaction

LEASE = timedelta(minutes=5)


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
