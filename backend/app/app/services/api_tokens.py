"""Personal access tokens (``tsk_...``) for MCP clients that can't do OAuth.

Some MCP callers can only send a static header: the xAI API's remote MCP
tool, CI scripts, headless agents. A user mints a token here and pastes it as
``Authorization: Bearer tsk_...``. Tokens work only on ``/mcp``, never on the
REST API (``api.deps`` doesn't know them), which limits what a leaked token
can do. Only the SHA-256 of a token is stored, and the plaintext is returned
once, by ``create_token``.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.core.config import settings
from app.services.errors import ConflictError, NotFoundError

TOKEN_PREFIX = "tsk_"
# The part of a token kept in clear to tell tokens apart: "tsk_" plus 8.
DISPLAY_PREFIX_LENGTH = 12
# last_used_at is written at most this often per token, not on every call.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate() -> tuple[str, str]:
    """A new token and its hash."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plaintext, hash_token(plaintext)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active(now: datetime):
    return (
        models.ApiToken.revoked_at.is_(None),
        (models.ApiToken.expires_at.is_(None)) | (models.ApiToken.expires_at > now),
    )


async def create_token(
    db: AsyncSession,
    user: models.User,
    name: str,
    scope: str,
    expires_in_days: int | None = None,
) -> tuple[models.ApiToken, str]:
    """Store a new token; returns it with its plaintext, which isn't kept."""
    # Lock the owner's row so concurrent creates can't both pass the cap.
    await crud.user.get_for_update(db, user_id=user.id)
    now = _now()
    active = await db.scalar(
        select(func.count())
        .select_from(models.ApiToken)
        .where(models.ApiToken.owner_id == user.id, *_active(now))
    )
    if active >= settings.API_TOKENS_MAX_PER_USER:
        raise ConflictError(
            f"You already have {settings.API_TOKENS_MAX_PER_USER} active tokens. "
            "Revoke one first."
        )
    plaintext, digest = generate()
    token = models.ApiToken(
        owner_id=user.id,
        name=name,
        token_hash=digest,
        prefix=plaintext[:DISPLAY_PREFIX_LENGTH],
        scope=scope,
        expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token, plaintext


async def list_tokens(db: AsyncSession, user: models.User) -> list[models.ApiToken]:
    """The user's tokens that still work, newest first."""
    result = await db.execute(
        select(models.ApiToken)
        .where(models.ApiToken.owner_id == user.id, *_active(_now()))
        .order_by(models.ApiToken.created_at.desc(), models.ApiToken.id.desc())
    )
    return list(result.scalars().all())


async def revoke_token(db: AsyncSession, user: models.User, token_id: int) -> None:
    token = await db.get(models.ApiToken, token_id)
    # Another user's token is reported as missing, so ids can't be probed.
    if token is None or token.owner_id != user.id or token.revoked_at is not None:
        raise NotFoundError("Token not found")
    token.revoked_at = _now()
    await db.commit()


async def resolve(db: AsyncSession, plaintext: str) -> models.ApiToken | None:
    """The live token with this plaintext, or None if unknown, revoked or expired."""
    now = _now()
    token = await db.scalar(
        select(models.ApiToken).where(
            models.ApiToken.token_hash == hash_token(plaintext), *_active(now)
        )
    )
    if token is None:
        return None
    if token.last_used_at is None or now - token.last_used_at >= LAST_USED_RESOLUTION:
        await db.execute(
            update(models.ApiToken)
            .where(models.ApiToken.id == token.id)
            .values(last_used_at=now)
        )
        await db.commit()
    return token
