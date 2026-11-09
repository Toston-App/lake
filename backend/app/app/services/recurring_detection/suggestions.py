"""Accepting a suggestion (a template from its newest transaction) and
dismissing one."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.ai import recurring_detection as ai
from app.core.recurrence_detection import (
    AMOUNT_TOLERANCE,
    Candidate,
    Merchant,
    PointKey,
    TxPoint,
    detect_series,
    group_key,
    point_key,
    text_key,
)
from app.services import recurring as recurring_service
from app.services.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    ServiceError,
)
from app.services.transaction_kinds import KINDS

from .history import (
    Dismissal,
    Template,
    _fallback,
    _load_aliases,
    _merchants_by_point,
    load_points,
    resolve_merchants,
)

logger = logging.getLogger(__name__)

# Newer transactions compared when an accepted one is outside any series.
NEWER_IN_GROUP_LIMIT = 500
MAX_DISMISSALS_PER_USER = 1000


async def _rollback(db: AsyncSession, user: models.User) -> None:
    """Roll back and reload the user, which the rollback expired."""
    await db.rollback()
    await db.refresh(user)


# ---------------------------------------------------------------------------
# Accept / dismiss
# ---------------------------------------------------------------------------
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

        self.fingerprints = {c.fingerprint for c in candidates}

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
    kind = KINDS[item.transaction_type]
    model = kind.model
    newer = (
        (
            await db.execute(
                select(model)
                .where(
                    model.owner_id == user.id,
                    model.recurring_id.is_(None),
                    model.date > tx.date,
                    model.id != tx.id,
                )
                .order_by(model.date.desc(), model.id.desc())
                .limit(NEWER_IN_GROUP_LIMIT)
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
        return kind.to_point(row, places.get(getattr(row, "place_id", None)))

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
    model = KINDS[item.transaction_type].model
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
    series = index.series_of((item.transaction_type, tx.id))
    if series is not None:
        if item.fingerprint != series.fingerprint:
            raise DomainError("The suggestion does not match this transaction")
        newer = series.latest if series.latest.id != tx.id else None
    else:
        # No detected series (e.g. the history changed since the scan). The
        # fingerprint can't be checked, but it must not be another series'
        # one, which would hide that series from later scans.
        if item.fingerprint in index.fingerprints:
            raise DomainError("The suggestion does not match this transaction")
        newer = await _newer_in_group(db, user, item, tx)
    if newer is not None:
        raise DomainError(
            "Use the latest transaction of the series "
            f"(found a newer one on {newer.date.isoformat()})"
        )


def _failed(fingerprint: str, error: str) -> schemas.RecurringSuggestionAcceptResult:
    return schemas.RecurringSuggestionAcceptResult(
        fingerprint=fingerprint, status="error", error=error
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
            results.append(_failed(item.fingerprint, exc.message))
        except IntegrityError:
            # A concurrent accept of the same suggestion won the unique index.
            await _rollback(db, user)
            results.append(
                _failed(item.fingerprint, "This suggestion was already added")
            )
        except Exception:
            logger.exception("accepting recurring suggestion failed")
            await _rollback(db, user)
            results.append(
                _failed(item.fingerprint, "Could not create this recurring transaction")
            )
    return results


async def dismiss_suggestions(
    db: AsyncSession,
    user: models.User,
    items: list[schemas.RecurringSuggestionDismissItem],
) -> int:
    fingerprints = {item.fingerprint for item in items}
    stored = await db.scalar(
        select(func.count()).select_from(Dismissal).where(Dismissal.owner_id == user.id)
    )
    already = await db.scalar(
        select(func.count())
        .select_from(Dismissal)
        .where(Dismissal.owner_id == user.id, Dismissal.fingerprint.in_(fingerprints))
    )
    if stored + len(fingerprints) - already > MAX_DISMISSALS_PER_USER:
        raise DomainError(
            f"You can dismiss at most {MAX_DISMISSALS_PER_USER} suggestions; "
            "undo some dismissals first"
        )
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
