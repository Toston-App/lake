"""Detect recurring series in a user's history and turn them into templates.

A scan loads the user's unlinked transactions, resolves each description to a
merchant (cached alias -> AI -> deterministic fallback), runs the pure
detection in `app.core.recurrence_detection`, drops what the user already has
or dismissed, optionally lets the AI classify what's left and returns ranked
suggestions. Only the last result is saved (`recurring_detection_usage`).

Access: paid tiers scan without limit. Free users get one scan that returns
suggestions; a scan that finds nothing doesn't use it up. A previous scan on
any tier counts, so a lapsed paid user who scanned has no free scan left.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.ai import recurring_detection as ai
from app.core.config import settings
from app.core.recurrence import RecurringType
from app.core.recurrence_detection import (
    AMOUNT_TOLERANCE,
    MAX_SUGGESTIONS,
    YEARLY_WINDOW_DAYS,
    Candidate,
    Merchant,
    PointKey,
    TxPoint,
    apply_classification,
    detect_series,
    group_key,
    point_key,
    slugify,
    text_key,
)
from app.core.tiers import FEATURE_RECURRING_DETECTION, has_feature
from app.db.session import async_session
from app.schemas.transaction import TransactionType
from app.services import recurring as recurring_service
from app.services.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    ServiceError,
)

logger = logging.getLogger(__name__)

Usage = models.RecurringDetectionUsage
Alias = models.RecurringMerchantAlias
Dismissal = models.RecurringSuggestionDismissal
Template = models.RecurringTransaction

AI_MERCHANT_CHUNK = 150
AI_MERCHANT_MAX_CHUNKS = 4
# A claim older than this belongs to a scan that crashed; it can be taken over.
STALE_CLAIM = timedelta(minutes=10)
# After a cancelled scan (e.g. a page reload) the next one waits this long, so
# cancelling can't be used to start scans back to back.
CANCEL_COOLDOWN = timedelta(minutes=1)
# The release after a cancel gives up after this, rather than holding a
# connection while it waits on a row lock.
RELEASE_TIMEOUT_S = 10
RUNNING = "running"
DONE = "done"
CANCELLED = "cancelled"


class ScanLimitError(ServiceError):
    """A free user has already used their scan."""

    status_code = 402

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

    def __init__(self, retry_after: int):
        super().__init__("A scan was just cancelled; try again shortly")
        self.retry_after = retry_after

    def detail(self) -> dict[str, Any]:
        return {"reason": "cancel_cooldown", "retry_after": self.retry_after}


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


def _remaining(unlimited: bool, usage: Usage | None) -> int | None:
    if unlimited:
        return None
    return 0 if usage is not None and usage.scan_count > 0 else 1


async def scans_remaining(db: AsyncSession, user: models.User) -> int | None:
    """1 or 0 for free users, None when the user's tier is unlimited."""
    unlimited = await has_unlimited_scans(db, user)
    if unlimited:
        return None
    return _remaining(False, await _usage(db, user))


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
        or_(Usage.status != RUNNING, Usage.claimed_at < now - STALE_CLAIM),
        or_(Usage.status != CANCELLED, Usage.claimed_at < now - CANCEL_COOLDOWN),
    )
    allowed = not_running if unlimited else and_(Usage.scan_count == 0, not_running)
    stmt = (
        insert(Usage)
        .values(owner_id=user.id, status=RUNNING, scan_count=0, claimed_at=now)
        .on_conflict_do_update(
            index_elements=[Usage.owner_id],
            set_={"status": RUNNING, "claimed_at": now},
            where=allowed,
        )
        .returning(Usage.owner_id)
    )
    claimed = (await db.execute(stmt)).scalar_one_or_none()
    await db.commit()
    if claimed is not None:
        return now
    usage = await _usage(db, user)
    if usage is not None:
        await db.refresh(usage)
    if not unlimited and usage is not None and usage.scan_count > 0:
        raise ScanLimitError(usage.last_scan_at)
    if usage is not None and usage.status == CANCELLED and usage.claimed_at:
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
    status: str = DONE,
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
    if status == CANCELLED:
        # The cooldown counts from the cancel, not from the scan's start.
        values["claimed_at"] = datetime.now(timezone.utc)
    updated = await db.execute(
        update(Usage)
        .where(
            Usage.owner_id == user_id,
            Usage.status == RUNNING,
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
            await _finish_scan(db, user_id, claimed_at=claimed_at, status=CANCELLED)

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


async def _rollback(db: AsyncSession, user: models.User) -> None:
    """Roll back and reload the user, which the rollback expired."""
    await db.rollback()
    await db.refresh(user)


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


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
async def load_points(
    db: AsyncSession, user: models.User, today: date
) -> list[TxPoint]:
    """Unlinked transactions from the last 25 months, one query per type."""
    since = today - timedelta(days=YEARLY_WINDOW_DAYS)
    points: list[TxPoint] = []

    expense = models.Expense
    rows = await db.execute(
        select(
            expense.id,
            expense.date,
            expense.amount,
            expense.description,
            expense.account_id,
            expense.category_id,
            expense.subcategory_id,
            expense.place_id,
            models.Place.name,
            models.Category.name,
        )
        .outerjoin(models.Place, models.Place.id == expense.place_id)
        .outerjoin(models.Category, models.Category.id == expense.category_id)
        .where(
            expense.owner_id == user.id,
            expense.recurring_id.is_(None),
            expense.date >= since,
            expense.date <= today,
            expense.amount > 0,
        )
    )
    for row in rows:
        points.append(
            TxPoint(
                id=row[0],
                type=RecurringType.expense,
                date=row[1],
                amount=float(row[2]),
                description=row[3],
                account_id=row[4],
                category_id=row[5],
                subcategory_id=row[6],
                place_id=row[7],
                place_name=row[8],
                category_name=row[9],
            )
        )

    income = models.Income
    rows = await db.execute(
        select(
            income.id,
            income.date,
            income.amount,
            income.description,
            income.account_id,
            income.subcategory_id,
            income.place_id,
            models.Place.name,
            models.Subcategory.name,
        )
        .outerjoin(models.Place, models.Place.id == income.place_id)
        .outerjoin(models.Subcategory, models.Subcategory.id == income.subcategory_id)
        .where(
            income.owner_id == user.id,
            income.recurring_id.is_(None),
            income.date >= since,
            income.date <= today,
            income.amount > 0,
        )
    )
    for row in rows:
        points.append(
            TxPoint(
                id=row[0],
                type=RecurringType.income,
                date=row[1],
                amount=float(row[2]),
                description=row[3],
                account_id=row[4],
                subcategory_id=row[5],
                place_id=row[6],
                place_name=row[7],
                category_name=row[8],
            )
        )

    transfer = models.Transfer
    rows = await db.execute(
        select(
            transfer.id,
            transfer.date,
            transfer.amount,
            transfer.description,
            transfer.from_acc,
            transfer.to_acc,
        ).where(
            transfer.owner_id == user.id,
            transfer.recurring_id.is_(None),
            transfer.date >= since,
            transfer.date <= today,
            transfer.amount > 0,
        )
    )
    for row in rows:
        points.append(
            TxPoint(
                id=row[0],
                type=RecurringType.transfer,
                date=row[1],
                amount=float(row[2]),
                description=row[3],
                account_id=row[4],
                to_account_id=row[5],
            )
        )
    return points


async def _load_templates(db: AsyncSession, user: models.User) -> list[TxPoint]:
    """Templates as points, so their text resolves like a transaction's.

    Templates created from a suggestion are left out: their fingerprint
    already hides their series, and matching them by merchant could hide the
    same merchant's other plans.
    """
    rows = await db.execute(
        select(
            Template.id,
            Template.type,
            Template.amount,
            Template.description,
            Template.account_id,
            Template.to_account_id,
            models.Place.name,
        )
        .outerjoin(models.Place, models.Place.id == Template.place_id)
        .where(Template.owner_id == user.id, Template.detection_fingerprint.is_(None))
    )
    return [
        TxPoint(
            id=row[0],
            type=RecurringType(row[1]),
            date=date.min,
            amount=float(row[2]),
            description=row[3],
            account_id=row[4],
            to_account_id=row[5],
            place_name=row[6],
        )
        for row in rows
    ]


async def _skipped_fingerprints(db: AsyncSession, user: models.User) -> set[str]:
    accepted = await db.execute(
        select(Template.detection_fingerprint).where(
            Template.owner_id == user.id, Template.detection_fingerprint.is_not(None)
        )
    )
    dismissed = await db.execute(
        select(Dismissal.fingerprint).where(Dismissal.owner_id == user.id)
    )
    return set(accepted.scalars()) | set(dismissed.scalars())


# ---------------------------------------------------------------------------
# Merchant resolution
# ---------------------------------------------------------------------------
def _fallback(text: str) -> Merchant:
    return Merchant(key=slugify(text) or text, name=text.title())


async def _load_aliases(
    db: AsyncSession, user: models.User, texts: set[str]
) -> dict[str, Merchant]:
    if not texts:
        return {}
    rows = await db.execute(
        select(
            Alias.normalized_description, Alias.merchant_key, Alias.merchant_name
        ).where(Alias.owner_id == user.id, Alias.normalized_description.in_(texts))
    )
    return {row[0]: Merchant(row[1], row[2]) for row in rows}


async def resolve_merchants(
    db: AsyncSession,
    user: models.User,
    points: list[TxPoint],
    *,
    use_ai: bool,
    stats: ai.AiStats,
) -> dict[str, Merchant]:
    """Normalized text -> merchant for every point that has text."""
    samples: dict[str, TxPoint] = {}
    for point in points:
        text = text_key(point)
        if text and text not in samples:
            samples[text] = point
    resolved = await _load_aliases(db, user, set(samples))
    missing = [text for text in samples if text not in resolved]

    if use_ai and missing:
        limit = AI_MERCHANT_CHUNK * AI_MERCHANT_MAX_CHUNKS
        batch = missing[:limit]
        for start in range(0, len(batch), AI_MERCHANT_CHUNK):
            chunk = batch[start : start + AI_MERCHANT_CHUNK]
            payload = [
                {
                    "i": i,
                    "type": samples[text].type.value,
                    "text": text,
                    "place": samples[text].place_name,
                }
                for i, text in enumerate(chunk)
            ]
            answers = await ai.resolve_merchants(payload, stats)
            new_rows = []
            for i, text in enumerate(chunk):
                answer = answers.get(i)
                if answer is None:
                    continue
                key = slugify(answer.merchant_key)
                name = answer.merchant_name.strip()[:120]
                if not key or not name:
                    continue
                resolved[text] = Merchant(key, name)
                new_rows.append(
                    {
                        "owner_id": user.id,
                        "normalized_description": text,
                        "merchant_key": key,
                        "merchant_name": name,
                        "source": "ai",
                    }
                )
            if new_rows:
                await db.execute(
                    insert(Alias)
                    .values(new_rows)
                    .on_conflict_do_nothing(
                        index_elements=[Alias.owner_id, Alias.normalized_description]
                    )
                )
                await db.commit()

    # Not saved: the AI gets another try on the next scan.
    for text in missing:
        resolved.setdefault(text, _fallback(text))
    return resolved


# ---------------------------------------------------------------------------
# Skipping what the user already has
# ---------------------------------------------------------------------------
def _series_key(candidate: Candidate) -> tuple:
    latest = candidate.latest
    merchant = candidate.merchant_key if candidate.has_text else None
    if candidate.type == RecurringType.transfer:
        return (candidate.type, latest.account_id, latest.to_account_id, merchant)
    return (candidate.type, merchant)


def _amount_distance(candidate: Candidate, amount: float) -> float:
    low, high = min(candidate.amounts), max(candidate.amounts)
    if low <= amount <= high:
        return 0.0
    return min(abs(amount - low), abs(amount - high))


def _drop_covered(
    candidates: list[Candidate],
    templates: list[TxPoint],
    merchants: dict[str, Merchant],
) -> list[Candidate]:
    """Drop the series the user already has as a template.

    Each template covers at most one series: of the series with its merchant
    (and accounts, for transfers), the one closest in amount, and only within
    a factor of 2 (price changes). So a template for a 2.99 plan doesn't hide
    the same merchant's 29.99 plan.
    """
    by_key: dict[tuple, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_key[_series_key(candidate)].append(candidate)
    covered: set[int] = set()

    def cover(matches: list[Candidate], amount: float) -> None:
        open_matches = [
            c
            for c in matches
            if id(c) not in covered
            and min(c.amounts) / 2 <= amount <= max(c.amounts) * 2
        ]
        if open_matches:
            best = min(open_matches, key=lambda c: _amount_distance(c, amount))
            covered.add(id(best))

    for template in templates:
        text = text_key(template)
        if text:
            merchant = merchants.get(text) or _fallback(text)
            key = group_key(template, merchant)
            matches = by_key.get(key, [])
            if not matches and template.type == RecurringType.transfer:
                # A described transfer template still covers an undescribed series.
                matches = by_key.get(key[:3] + (None,), [])
            cover(matches, template.amount)
            continue
        # No text: same type and accounts, amount within ±10%.
        matches = [
            c
            for c in candidates
            if c.type == template.type
            and c.latest.account_id == template.account_id
            and (
                template.type != RecurringType.transfer
                or c.latest.to_account_id == template.to_account_id
            )
            and abs(template.amount - c.median_amount)
            <= AMOUNT_TOLERANCE * c.median_amount
        ]
        cover(matches, template.amount)
    return [c for c in candidates if id(c) not in covered]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def _to_suggestion(candidate: Candidate) -> schemas.RecurringSuggestion:
    latest = candidate.latest
    detected = candidate.detected
    kind = candidate.kind
    if kind == "habit":
        kind = "other"
    return schemas.RecurringSuggestion(
        fingerprint=candidate.fingerprint,
        type=candidate.type,
        merchant_name=candidate.merchant_name,
        description=latest.description,
        amount=round(latest.amount, 2),
        amount_min=round(min(candidate.amounts), 2),
        amount_max=round(max(candidate.amounts), 2),
        account_id=latest.account_id,
        to_account_id=latest.to_account_id,
        category_id=latest.category_id,
        subcategory_id=latest.subcategory_id,
        place_id=latest.place_id,
        frequency=detected.frequency,
        interval=detected.interval,
        weekdays=list(detected.weekdays),
        month_days=list(detected.month_days),
        business_day_adjustment=detected.adjustment,
        occurrences=[
            schemas.recurring_transaction.SuggestionOccurrence(
                transaction_id=p.id, date=p.date, amount=round(p.amount, 2)
            )
            for p in reversed(candidate.points)
        ],
        last_date=latest.date,
        next_expected_date=candidate.next_expected_date,
        confidence=round(candidate.confidence, 3),
        kind=kind,
        reasons=candidate.reasons,
    )


async def _classify(
    candidates: list[Candidate], stats: ai.AiStats
) -> list[Candidate] | None:
    payload = [
        {
            "i": i,
            "type": c.type.value,
            "merchant_name": c.merchant_name,
            "category_name": c.latest.category_name,
            "frequency": c.detected.frequency.value,
            "interval": c.detected.interval,
            "occurrences": len(c.points),
            "amount_min": round(min(c.amounts), 2),
            "amount_max": round(max(c.amounts), 2),
        }
        for i, c in enumerate(candidates)
    ]
    errors = stats.errors
    labels = await ai.classify_candidates(payload, stats)
    if stats.errors > errors:
        return None
    out = []
    for i, candidate in enumerate(candidates):
        label = labels.get(i)
        if label is None:
            out.append(candidate)
            continue
        out.append(
            apply_classification(
                candidate,
                is_commitment=label.is_commitment,
                confidence=label.confidence,
                kind=label.kind,
            )
        )
    return out


def _merchants_by_point(
    points: list[TxPoint], merchants_by_text: dict[str, Merchant]
) -> dict[PointKey, Merchant]:
    return {
        point_key(p): merchants_by_text[text]
        for p in points
        if (text := text_key(p)) and text in merchants_by_text
    }


@dataclass
class ScanResult:
    result: schemas.RecurringDetectionResult
    stats: ai.AiStats
    candidates: int


async def detect(
    db: AsyncSession,
    user: models.User,
    *,
    use_ai: bool = True,
    today: date | None = None,
) -> ScanResult:
    """Run one scan. Saves merchant aliases, nothing else."""
    today = today or recurring_service.local_today("UTC")
    use_ai = use_ai and settings.RECURRING_DETECTION_AI_ENABLED
    stats = ai.AiStats()

    points = await load_points(db, user, today)
    templates = await _load_templates(db, user)
    merchants_by_text = await resolve_merchants(
        db, user, points + templates, use_ai=use_ai, stats=stats
    )
    merchants = _merchants_by_point(points, merchants_by_text)

    skipped = await _skipped_fingerprints(db, user)
    # Templates first, so a template only ever covers the series it matches
    # best, then what was accepted (fingerprint) or dismissed.
    candidates = _drop_covered(
        detect_series(points, merchants, today), templates, merchants_by_text
    )
    candidates = [c for c in candidates if c.fingerprint not in skipped]
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    candidates = candidates[:MAX_SUGGESTIONS]
    found = len(candidates)

    if use_ai and candidates:
        classified = await _classify(candidates, stats)
        if classified is not None:
            candidates = classified
    candidates = [
        c
        for c in candidates
        if c.confidence >= settings.RECURRING_DETECTION_MIN_CONFIDENCE
    ]
    candidates.sort(key=lambda c: c.confidence, reverse=True)

    result = schemas.RecurringDetectionResult(
        suggestions=[_to_suggestion(c) for c in candidates],
        ai_used=use_ai and stats.calls > 0 and stats.errors == 0,
        scanned_transactions=len(points),
        scanned_at=datetime.now(timezone.utc),
    )
    return ScanResult(result=result, stats=stats, candidates=found)


async def check_access(db: AsyncSession, user: models.User) -> bool:
    """Fail fast with ScanLimitError; returns whether scans are unlimited.

    Only a pre-check (e.g. before the rate limit): `claim_scan` is the
    atomic gate.
    """
    unlimited = await has_unlimited_scans(db, user)
    if not unlimited:
        usage = await _usage(db, user)
        if usage is not None and usage.scan_count > 0:
            raise ScanLimitError(usage.last_scan_at)
    return unlimited


async def run_scan(
    db: AsyncSession,
    user: models.User,
    *,
    use_ai: bool = True,
    today: date | None = None,
    unlimited: bool | None = None,
) -> ScanResult:
    """Claim the user's scan slot, scan, and save the result.

    A scan only uses up the free scan when it returns suggestions. If the scan
    or saving its result fails, or the request is cancelled, the slot is
    released without counting.
    """
    user_id = user.id
    if unlimited is None:
        unlimited = await has_unlimited_scans(db, user)
    claimed_at = await claim_scan(db, user, unlimited=unlimited)
    try:
        scan = await detect(db, user, use_ai=use_ai, today=today)
        counted = bool(scan.result.suggestions)
        scan.result.scans_remaining = None if unlimited else (0 if counted else 1)
        await _finish_scan(
            db,
            user_id,
            claimed_at=claimed_at,
            result=scan.result.model_dump(mode="json"),
            counted=counted,
        )
    except asyncio.CancelledError:
        # The client went away (e.g. a page reload). Free the claim right
        # away so the status stops reporting a scan that isn't running.
        _release_in_background(user_id, claimed_at)
        raise
    except Exception:
        await release_scan(db, user_id, claimed_at)
        raise
    return scan


async def scan_status(
    db: AsyncSession, user: models.User
) -> schemas.RecurringDetectionStatus:
    """Whether a scan is running, so a client that lost its request can wait."""
    usage = await _usage(db, user)
    stale_before = datetime.now(timezone.utc) - STALE_CLAIM
    running = (
        usage is not None
        and usage.status == RUNNING
        and usage.claimed_at is not None
        and usage.claimed_at >= stale_before
    )
    unlimited = await has_unlimited_scans(db, user)
    return schemas.RecurringDetectionStatus(
        running=running,
        last_scan_at=usage.last_scan_at if usage else None,
        scans_remaining=_remaining(unlimited, usage),
    )


async def last_result(
    db: AsyncSession, user: models.User
) -> schemas.RecurringDetectionResult:
    """The last scan's result, without what was accepted or dismissed since."""
    usage = await _usage(db, user)
    if usage is None or usage.last_result is None:
        raise NotFoundError("No scan yet")
    result = schemas.RecurringDetectionResult.model_validate(usage.last_result)
    skipped = await _skipped_fingerprints(db, user)
    result.suggestions = [s for s in result.suggestions if s.fingerprint not in skipped]
    unlimited = await has_unlimited_scans(db, user)
    result.scans_remaining = _remaining(unlimited, usage)
    return result


# ---------------------------------------------------------------------------
# Accept / dismiss
# ---------------------------------------------------------------------------
_TYPE_MODELS = {
    TransactionType.expense: models.Expense,
    TransactionType.income: models.Income,
    TransactionType.transfer: models.Transfer,
}


def _point_from(
    transaction_type: TransactionType, tx: Any, place_name: str | None
) -> TxPoint:
    is_transfer = transaction_type == TransactionType.transfer
    return TxPoint(
        id=tx.id,
        type=RecurringType(transaction_type.value),
        date=tx.date,
        amount=float(tx.amount),
        description=tx.description,
        place_name=place_name,
        account_id=tx.from_acc if is_transfer else tx.account_id,
        to_account_id=tx.to_acc if is_transfer else None,
        category_id=getattr(tx, "category_id", None),
        subcategory_id=None if is_transfer else tx.subcategory_id,
    )


async def _place_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.execute(
        select(models.Place.id, models.Place.name).where(models.Place.id.in_(ids))
    )
    return {row[0]: row[1] for row in rows}


class _SeriesIndex:
    """Which detected series each unlinked transaction belongs to."""

    def __init__(self, candidates: list[Candidate]):
        self._series: dict[PointKey, Candidate] = {}
        for candidate in candidates:
            for point in candidate.points:
                self._series[point_key(point)] = candidate

    def series_of(self, key: PointKey) -> Candidate | None:
        return self._series.get(key)


async def _build_series_index(db: AsyncSession, user: models.User) -> _SeriesIndex:
    """Detect series the way a scan does, from cached aliases only (no AI)."""
    today = recurring_service.local_today("UTC")
    points = await load_points(db, user, today)
    merchants_by_text = await resolve_merchants(
        db, user, points, use_ai=False, stats=ai.AiStats()
    )
    merchants = _merchants_by_point(points, merchants_by_text)
    return _SeriesIndex(detect_series(points, merchants, today))


async def _newer_in_group(
    db: AsyncSession, user: models.User, item: schemas.RecurringSuggestionAccept, tx
) -> TxPoint | None:
    """Fallback for a transaction outside any detected series: a newer one
    from the same merchant within the amount tolerance."""
    model = _TYPE_MODELS[item.transaction_type]
    newer = (
        (
            await db.execute(
                select(model).where(
                    model.owner_id == user.id,
                    model.recurring_id.is_(None),
                    model.date > tx.date,
                    model.id != tx.id,
                )
            )
        )
        .scalars()
        .all()
    )
    if not newer:
        return None
    place_ids = {
        p.place_id for p in [tx, *newer] if getattr(p, "place_id", None) is not None
    }
    places = await _place_names(db, place_ids)

    def point(row: Any) -> TxPoint:
        return _point_from(
            item.transaction_type, row, places.get(getattr(row, "place_id", None))
        )

    source = point(tx)
    others = [point(row) for row in newer]
    texts = {text_key(p) for p in [source, *others]} - {""}
    aliases = await _load_aliases(db, user, texts)

    def merchant_of(p: TxPoint) -> Merchant | None:
        text = text_key(p)
        return (aliases.get(text) or _fallback(text)) if text else None

    merchant = merchant_of(source)
    key = group_key(source, merchant)
    tolerance = 0.01 if merchant is None else AMOUNT_TOLERANCE * source.amount
    for other in others:
        if group_key(other, merchant_of(other)) != key:
            continue
        if abs(other.amount - source.amount) <= tolerance:
            return other
    return None


async def _check_latest(
    db: AsyncSession,
    user: models.User,
    item: schemas.RecurringSuggestionAccept,
    index: _SeriesIndex,
) -> None:
    """The transaction must be the newest of its series.

    An older one would make the new template back-fill copies of charges the
    user already has. The series is the detected one, so it spans price
    changes but not the same merchant's other plans.
    """
    model = _TYPE_MODELS[item.transaction_type]
    tx = (
        await db.execute(
            select(model).where(
                model.id == item.transaction_id, model.owner_id == user.id
            )
        )
    ).scalar_one_or_none()
    if tx is None:
        raise NotFoundError("Transaction not found")
    if tx.recurring_id:
        raise DomainError("This transaction already belongs to a recurring series")
    series = index.series_of((RecurringType(item.transaction_type.value), tx.id))
    if series is not None:
        newer = series.latest if series.latest.id != tx.id else None
    else:
        newer = await _newer_in_group(db, user, item, tx)
    if newer is not None:
        raise DomainError(
            "Use the latest transaction of the series "
            f"(found a newer one on {newer.date.isoformat()})"
        )


async def accept_suggestions(
    db: AsyncSession,
    user: models.User,
    items: list[schemas.RecurringSuggestionAccept],
) -> list[schemas.RecurringSuggestionAcceptResult]:
    """Create a template per suggestion; each item succeeds or fails on its own."""
    results = []
    index = await _build_series_index(db, user)
    for item in items:
        try:
            exists = await db.execute(
                select(Template.id).where(
                    Template.owner_id == user.id,
                    Template.detection_fingerprint == item.fingerprint,
                )
            )
            if exists.first() is not None:
                raise ConflictError("This suggestion was already added")
            await _check_latest(db, user, item, index)
            template = await recurring_service.create_from_transaction(
                db, user, item, detection_fingerprint=item.fingerprint
            )
            results.append(
                schemas.RecurringSuggestionAcceptResult(
                    fingerprint=item.fingerprint,
                    status="created",
                    recurring=recurring_service.to_detail(template),
                )
            )
        except ServiceError as exc:
            # Validation fails before anything is written.
            results.append(
                schemas.RecurringSuggestionAcceptResult(
                    fingerprint=item.fingerprint, status="error", error=exc.message
                )
            )
        except IntegrityError:
            # A concurrent accept of the same suggestion won the unique index.
            await _rollback(db, user)
            results.append(
                schemas.RecurringSuggestionAcceptResult(
                    fingerprint=item.fingerprint,
                    status="error",
                    error="This suggestion was already added",
                )
            )
        except Exception:
            logger.exception("accepting recurring suggestion failed")
            await _rollback(db, user)
            results.append(
                schemas.RecurringSuggestionAcceptResult(
                    fingerprint=item.fingerprint,
                    status="error",
                    error="Could not create this recurring transaction",
                )
            )
    return results


async def dismiss_suggestions(
    db: AsyncSession,
    user: models.User,
    items: list[schemas.RecurringSuggestionDismissItem],
) -> int:
    rows = list(
        {
            item.fingerprint: {
                "owner_id": user.id,
                "fingerprint": item.fingerprint,
                "label": item.label,
            }
            for item in items
        }.values()
    )
    result = await db.execute(
        insert(Dismissal)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=[Dismissal.owner_id, Dismissal.fingerprint]
        )
        .returning(Dismissal.id)
    )
    dismissed = len(result.all())
    await db.commit()
    return dismissed


async def undo_dismissal(db: AsyncSession, user: models.User, fingerprint: str) -> None:
    result = await db.execute(
        delete(Dismissal)
        .where(Dismissal.owner_id == user.id, Dismissal.fingerprint == fingerprint)
        .returning(Dismissal.id)
    )
    if result.first() is None:
        raise NotFoundError("Dismissal not found")
    await db.commit()
