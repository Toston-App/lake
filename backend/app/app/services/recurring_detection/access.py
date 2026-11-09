"""Who may scan, and the per-user scan slot (`recurring_detection_usage`).

A scan claims the slot atomically before it runs and finishes or releases
it after; a claim that crashed goes stale and can be taken over.
"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.core.tiers import FEATURE_RECURRING_DETECTION, has_feature
from app.db.session import async_session
from app.services.errors import ConflictError, ServiceError

logger = logging.getLogger(__name__)

Usage = models.RecurringDetectionUsage

# A claim older than this belongs to a scan that crashed; it can be taken over.
STALE_CLAIM = timedelta(minutes=10)
# After a cancelled scan (e.g. a page reload) the next one waits this long, so
# cancelling can't be used to start scans back to back.
CANCEL_COOLDOWN = timedelta(minutes=1)
# The release after a cancel gives up after this, rather than holding a
# connection while it waits on a row lock.
RELEASE_TIMEOUT_S = 10
# Scans a free user gets.
FREE_SCANS = 1


class ScanStatus(str, Enum):
    """`recurring_detection_usage.status`; stored as these strings."""

    running = "running"
    done = "done"
    cancelled = "cancelled"


class ScanLimitError(ServiceError):
    """A free user has already used their scan."""

    status_code = 402
    event_outcome = "free_scan_used"

    def __init__(self, last_scan_at: datetime | None):
        super().__init__("Recurring detection is limited to one scan on the free plan")
        self.last_scan_at = last_scan_at

    def detail(self) -> dict[str, Any]:
        return {
            "reason": "recurring_detection_limit",
            "message": self.message,
            "tier": "free",
            "last_scan_at": self.last_scan_at.isoformat()
            if self.last_scan_at
            else None,
            "upgrade_url": "/account",
        }


class ScanCooldownError(ServiceError):
    """The last scan was cancelled moments ago."""

    status_code = 429
    event_outcome = "cancel_cooldown"

    def __init__(self, retry_after: int):
        super().__init__("A scan was just cancelled; try again shortly")
        self.retry_after = retry_after

    def detail(self) -> dict[str, Any]:
        return {"reason": "cancel_cooldown", "retry_after": self.retry_after}

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
async def has_unlimited_scans(db: AsyncSession, user: models.User) -> bool:
    if crud.user.is_superuser(user):
        return True
    from app.services.entitlements import get_user_tier

    tier, _ = await get_user_tier(db, user.id)
    return has_feature(tier, FEATURE_RECURRING_DETECTION)


async def _usage(db: AsyncSession, user: models.User) -> Usage | None:
    result = await db.execute(select(Usage).where(Usage.owner_id == user.id))
    return result.scalar_one_or_none()


def _remaining(unlimited: bool, scan_count: int) -> int | None:
    """Scans left after `scan_count` counted ones: 1 or 0 for free users,
    None when the user's tier is unlimited."""
    if unlimited:
        return None
    return max(0, FREE_SCANS - scan_count)


def _scan_count(usage: Usage | None) -> int:
    return usage.scan_count if usage is not None else 0


def _check_limit(unlimited: bool, usage: Usage | None) -> None:
    """Raise ScanLimitError when a free user has no scan left."""
    if _remaining(unlimited, _scan_count(usage)) == 0:
        raise ScanLimitError(usage.last_scan_at if usage else None)


async def scans_remaining(db: AsyncSession, user: models.User) -> int | None:
    """1 or 0 for free users, None when the user's tier is unlimited."""
    unlimited = await has_unlimited_scans(db, user)
    usage = None if unlimited else await _usage(db, user)
    return _remaining(unlimited, _scan_count(usage))


async def claim_scan(
    db: AsyncSession, user: models.User, *, unlimited: bool
) -> datetime:
    """Take the user's scan slot, atomically, before running a scan.

    Returns the claim's `claimed_at`, which identifies it: finishing or
    releasing only touches the row while it still holds this claim.

    Raises ScanLimitError when a free user has used their scan,
    ScanCooldownError right after a cancelled scan and ConflictError when
    another scan for this user is still running.
    """
    now = datetime.now(timezone.utc)
    not_running = and_(
        or_(Usage.status != ScanStatus.running, Usage.claimed_at < now - STALE_CLAIM),
        or_(
            Usage.status != ScanStatus.cancelled,
            Usage.claimed_at < now - CANCEL_COOLDOWN,
        ),
    )
    # The SQL side of `_check_limit`, so the claim is atomic.
    allowed = (
        not_running if unlimited else and_(Usage.scan_count < FREE_SCANS, not_running)
    )
    stmt = (
        insert(Usage)
        .values(
            owner_id=user.id, status=ScanStatus.running, scan_count=0, claimed_at=now
        )
        .on_conflict_do_update(
            index_elements=[Usage.owner_id],
            set_={"status": ScanStatus.running, "claimed_at": now},
            where=allowed,
        )
        .returning(Usage.owner_id)
    )
    claimed = (await db.execute(stmt)).scalar_one_or_none()
    try:
        await db.commit()
    except asyncio.CancelledError:
        # The commit may have reached the server before the cancel did: free
        # the claim, or it would block scans until it goes stale. The release
        # only touches a row holding this claim, so it's a no-op otherwise.
        if claimed is not None:
            _release_in_background(user.id, now)
        raise
    if claimed is not None:
        return now
    usage = await _usage(db, user)
    if usage is not None:
        await db.refresh(usage)
    _check_limit(unlimited, usage)
    if usage is not None and usage.status == ScanStatus.cancelled and usage.claimed_at:
        wait = usage.claimed_at + CANCEL_COOLDOWN - now
        raise ScanCooldownError(max(1, math.ceil(wait.total_seconds())))
    raise ConflictError("A scan is already running")


async def _finish_scan(
    db: AsyncSession,
    user_id: int,
    *,
    claimed_at: datetime,
    result: dict[str, Any] | None = None,
    counted: bool = False,
    status: ScanStatus = ScanStatus.done,
) -> bool:
    """End the scan holding `claimed_at`. False if that claim was lost.

    A claim is lost when a newer scan took it over after it went stale; the
    row then belongs to that scan and is left alone.
    """
    values: dict[str, Any] = {"status": status}
    if result is not None:
        values.update(last_result=result, last_scan_at=datetime.now(timezone.utc))
    if counted:
        values["scan_count"] = Usage.scan_count + 1
    if status == ScanStatus.cancelled:
        # The cooldown counts from the cancel, not from the scan's start.
        values["claimed_at"] = datetime.now(timezone.utc)
    updated = await db.execute(
        update(Usage)
        .where(
            Usage.owner_id == user_id,
            Usage.status == ScanStatus.running,
            Usage.claimed_at == claimed_at,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if updated.rowcount == 0:
        logger.warning("Recurring scan of user %s lost its claim", user_id)
        return False
    return True


def _cleanup_session() -> AbstractAsyncContextManager[AsyncSession]:
    """A session of its own: the request's may be closed by the cancellation."""
    return async_session()


# Strong references, so cleanup tasks aren't garbage collected mid-run.
_cleanup_tasks: set[asyncio.Task] = set()


async def _release_after_cancel(user_id: int, claimed_at: datetime) -> None:
    async def release() -> None:
        async with _cleanup_session() as db:
            await _finish_scan(
                db, user_id, claimed_at=claimed_at, status=ScanStatus.cancelled
            )

    try:
        await asyncio.wait_for(release(), RELEASE_TIMEOUT_S)
    except Exception:
        # The claim then goes stale on its own (STALE_CLAIM).
        logger.exception("Could not release the recurring scan of user %s", user_id)


def _release_in_background(user_id: int, claimed_at: datetime) -> None:
    """Release the claim even though the current task is being cancelled.

    Every await in a cancelled task is cancelled again, so the release runs
    as its own task.
    """
    task = asyncio.create_task(_release_after_cancel(user_id, claimed_at))
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


async def wait_for_cleanup() -> None:
    """Let pending releases finish, e.g. on shutdown."""
    if _cleanup_tasks:
        await asyncio.gather(*_cleanup_tasks, return_exceptions=True)


async def release_scan(db: AsyncSession, user_id: int, claimed_at: datetime) -> None:
    """Give the slot back after a failed scan, without using up the free one.

    Rolls back only if the session is unusable: everything the scan wrote
    (merchant aliases) was already committed.
    """
    try:
        await _finish_scan(db, user_id, claimed_at=claimed_at)
    except Exception:
        await db.rollback()
        await _finish_scan(db, user_id, claimed_at=claimed_at)


async def check_access(db: AsyncSession, user: models.User) -> bool:
    """Fail fast with ScanLimitError; returns whether scans are unlimited.

    Only a pre-check (e.g. before the rate limit): `claim_scan` is the
    atomic gate.
    """
    unlimited = await has_unlimited_scans(db, user)
    if not unlimited:
        _check_limit(False, await _usage(db, user))
    return unlimited


async def scan_status(
    db: AsyncSession, user: models.User
) -> schemas.RecurringDetectionStatus:
    """Whether a scan is running, so a client that lost its request can wait."""
    usage = await _usage(db, user)
    stale_before = datetime.now(timezone.utc) - STALE_CLAIM
    running = (
        usage is not None
        and usage.status == ScanStatus.running
        and usage.claimed_at is not None
        and usage.claimed_at >= stale_before
    )
    unlimited = await has_unlimited_scans(db, user)
    return schemas.RecurringDetectionStatus(
        running=running,
        last_scan_at=usage.last_scan_at if usage else None,
        scans_remaining=_remaining(unlimited, _scan_count(usage)),
    )
