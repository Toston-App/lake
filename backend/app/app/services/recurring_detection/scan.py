"""One scan: detect series, drop what the user already has, classify and
rank what's left, and save the result."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.ai import recurring_detection as ai
from app.core.config import settings
from app.core.recurrence_detection import (
    AMOUNT_TOLERANCE,
    MAX_SUGGESTIONS,
    Candidate,
    Merchant,
    TxPoint,
    _within,
    apply_classification,
    detect_series,
    group_key,
    text_key,
)
from app.schemas.transaction import TransactionType
from app.services import recurring as recurring_service
from app.services.errors import ConflictError, NotFoundError

from .access import (
    _finish_scan,
    _release_in_background,
    _remaining,
    _scan_count,
    _usage,
    claim_scan,
    has_unlimited_scans,
    release_scan,
)
from .history import (
    _fallback,
    _load_templates,
    _merchants_by_point,
    _release_connection,
    _skipped_fingerprints,
    load_points,
    resolve_merchants,
)


# ---------------------------------------------------------------------------
# Skipping what the user already has
# ---------------------------------------------------------------------------
def _series_key(candidate: Candidate) -> tuple:
    merchant = (
        Merchant(candidate.merchant_key, candidate.merchant_name)
        if candidate.has_text
        else None
    )
    return group_key(candidate.latest, merchant)


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
            if not matches and template.type == TransactionType.transfer:
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
                template.type != TransactionType.transfer
                or c.latest.to_account_id == template.to_account_id
            )
            and _within(template.amount, c.median_amount, AMOUNT_TOLERANCE, False)
        ]
        cover(matches, template.amount)
    return [c for c in candidates if id(c) not in covered]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def _to_suggestion(candidate: Candidate) -> schemas.RecurringSuggestion:
    latest = candidate.latest
    detected = candidate.detected
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
        kind=candidate.kind,
        reasons=candidate.reasons,
    )


async def _classify(
    db: AsyncSession, candidates: list[Candidate], stats: ai.AiStats
) -> list[Candidate] | None:
    summaries = [
        ai.CandidateSummary(
            type=c.type.value,
            merchant_name=c.merchant_name,
            category_name=c.latest.category_name,
            frequency=c.detected.frequency.value,
            interval=c.detected.interval,
            occurrences=len(c.points),
            amount_min=round(min(c.amounts), 2),
            amount_max=round(max(c.amounts), 2),
        )
        for c in candidates
    ]
    await _release_connection(db)
    labels = await ai.classify_candidates(summaries, stats)
    if labels is None:
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
        classified = await _classify(db, candidates, stats)
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


async def run_scan(
    db: AsyncSession,
    user: models.User,
    *,
    use_ai: bool = True,
    ai_allowed: bool = True,
    today: date | None = None,
    unlimited: bool | None = None,
) -> ScanResult:
    """Claim the user's scan slot, scan, and save the result.

    A scan only uses up the free scan when it returns suggestions. If the scan
    or saving its result fails, or the request is cancelled, the slot is
    released without counting. Raises ConflictError, without saving, when
    the claim went stale and a newer scan took it over.

    With `ai_allowed` False (the rate limiter is down) the scan runs without
    the AI even when `use_ai` asks for it: unthrottled AI calls cost money.
    """
    user_id = user.id
    if unlimited is None:
        unlimited = await has_unlimited_scans(db, user)
    claimed_at = await claim_scan(db, user, unlimited=unlimited)
    try:
        scan = await detect(db, user, use_ai=use_ai and ai_allowed, today=today)
        counted = bool(scan.result.suggestions)
        # The claim only lets a free user in with no counted scan.
        scan.result.scans_remaining = _remaining(unlimited, int(counted))
        saved = await _finish_scan(
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
    if not saved:
        # The claim went stale and a newer scan took it over: nothing was
        # saved or counted, and `/detect/last` will show the newer scan.
        raise ConflictError("Another scan took over; this result was not saved")
    return scan


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
    result.scans_remaining = _remaining(unlimited, _scan_count(usage))
    return result
