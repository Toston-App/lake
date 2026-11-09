"""What a scan reads: the user's transactions, templates and skipped
fingerprints, and the merchant each description resolves to (cached alias ->
AI -> deterministic fallback)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai import recurring_detection as ai
from app.core.recurrence_detection import (
    YEARLY_WINDOW_DAYS,
    Merchant,
    PointKey,
    TxPoint,
    point_key,
    slugify,
    text_key,
)
from app.services.transaction_kinds import KINDS

Alias = models.RecurringMerchantAlias
Dismissal = models.RecurringSuggestionDismissal
Template = models.RecurringTransaction

AI_MERCHANT_CHUNK = 150
AI_MERCHANT_MAX_CHUNKS = 4


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
async def load_points(
    db: AsyncSession, user: models.User, today: date
) -> list[TxPoint]:
    """Unlinked transactions from the last 25 months, one query per type.

    Ordered by type (as in KINDS), then date and id: the order decides how
    series are grouped and what is sent to the AI, so it must not change
    from one scan to the next.
    """
    since = today - timedelta(days=YEARLY_WINDOW_DAYS)
    points: list[TxPoint] = []
    for kind in KINDS.values():
        model = kind.model
        query = select(
            model.id.label("id"),
            model.date.label("date"),
            model.amount.label("amount"),
            model.description.label("description"),
            *(kind.column(field).label(field) for field in kind.columns),
        )
        if "place_id" in kind.columns:
            query = query.add_columns(models.Place.name.label("place_name")).outerjoin(
                models.Place, models.Place.id == kind.column("place_id")
            )
        if kind.name_source:
            field, named = kind.name_source
            query = query.add_columns(named.name.label("category_name")).outerjoin(
                named, named.id == kind.column(field)
            )
        rows = await db.execute(
            query.where(
                model.owner_id == user.id,
                model.recurring_id.is_(None),
                model.date >= since,
                model.date <= today,
                model.amount > 0,
            ).order_by(model.date, model.id)
        )
        points.extend(
            TxPoint(type=kind.type, **{**row._mapping, "amount": float(row.amount)})
            for row in rows
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
            type=row[1],
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
async def _release_connection(db: AsyncSession) -> None:
    """End the transaction so no connection is held while awaiting the AI.

    An AI call can take up to RECURRING_DETECTION_AI_TIMEOUT_SECONDS; holding
    a pooled connection idle in transaction meanwhile starves the pool. The
    transaction is only reads (or already committed aliases), and the session
    doesn't expire on commit, so loaded objects such as the user stay usable.
    The next query checks out a connection again.
    """
    await db.commit()


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
        resolved |= await _resolve_with_ai(db, user, samples, missing, stats)
    # Not saved: the AI gets another try on the next scan.
    for text in missing:
        resolved.setdefault(text, _fallback(text))
    return resolved


async def _resolve_with_ai(
    db: AsyncSession,
    user: models.User,
    samples: dict[str, TxPoint],
    texts: list[str],
    stats: ai.AiStats,
) -> dict[str, Merchant]:
    """Ask the AI in chunks and save its answers as aliases; texts it fails or
    doesn't answer are left out."""
    resolved: dict[str, Merchant] = {}
    batch = texts[: AI_MERCHANT_CHUNK * AI_MERCHANT_MAX_CHUNKS]
    for start in range(0, len(batch), AI_MERCHANT_CHUNK):
        chunk = batch[start : start + AI_MERCHANT_CHUNK]
        queries = [
            ai.MerchantQuery(
                type=samples[text].type.value,
                text=text,
                place=samples[text].place_name,
            )
            for text in chunk
        ]
        await _release_connection(db)
        answers = await ai.resolve_merchants(queries, stats)
        if answers is None:
            continue
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
    return resolved


def _merchants_by_point(
    points: list[TxPoint], merchants_by_text: dict[str, Merchant]
) -> dict[PointKey, Merchant]:
    return {
        point_key(p): merchants_by_text[text]
        for p in points
        if (text := text_key(p)) and text in merchants_by_text
    }
