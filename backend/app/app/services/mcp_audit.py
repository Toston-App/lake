"""The MCP audit log: which client called which tool for a user, and the outcome.

Recording never fails a tool call: a write error is logged and dropped. Each
write also deletes that user's rows older than MCP_AUDIT_RETENTION_DAYS, an
index range scan on ``(user_id, created_at)``, so the log stays bounded
without a separate cleanup job.

``list_connections`` reads the log back for the "AI connections" settings
page: one row per client a user has connected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.core.config import settings
from app.db.session import async_session

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 300
PAT_CLIENT_PREFIX = "pat:"
OAUTH_CLIENT_PREFIX = "oauth"
RECENT_DAYS = 30
# Calls refused before running don't count toward a plan's monthly cap.
_UNCOUNTED_STATUSES = ("refused",)


def _retention_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(
        days=settings.MCP_AUDIT_RETENTION_DAYS
    )


async def record_call(
    *,
    user_id: int,
    client: str | None,
    tool_name: str,
    status: str,
    duration_ms: int | None = None,
    action_id: str | None = None,
    error: str | None = None,
) -> None:
    try:
        async with async_session() as db:
            db.add(
                models.McpToolCall(
                    user_id=user_id,
                    client=client[:220] if client else None,
                    tool_name=tool_name[:100],
                    status=status,
                    action_id=action_id,
                    duration_ms=duration_ms,
                    error=error[:MAX_ERROR_LENGTH] if error else None,
                )
            )
            await prune(db, user_id)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - auditing must not break the call
        logger.warning("MCP audit write failed: %s", type(exc).__name__)


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def calls_this_month(db: AsyncSession, user_id: int) -> int:
    """MCP calls that ran (or were staged) this calendar month, UTC."""
    call = models.McpToolCall
    return await db.scalar(
        select(func.count())
        .select_from(call)
        .where(
            call.user_id == user_id,
            call.created_at >= month_start(),
            call.status.not_in(_UNCOUNTED_STATUSES),
        )
    )


async def prune(db: AsyncSession, user_id: int) -> None:
    """Delete the user's audit rows past the retention period (not committed)."""
    await db.execute(
        delete(models.McpToolCall).where(
            models.McpToolCall.user_id == user_id,
            models.McpToolCall.created_at < _retention_cutoff(),
        )
    )


@dataclass(frozen=True)
class Connection:
    """A client that called MCP for the user within the retention period."""

    client: str | None
    kind: str  # "oauth" or "token"
    last_used_at: datetime
    calls_last_30_days: int
    # For a personal access token: its id and name, and whether it still works.
    token_id: int | None = None
    token_name: str | None = None
    token_active: bool | None = None


async def list_connections(db: AsyncSession, user: models.User) -> list[Connection]:
    """The user's MCP clients, most recently used first."""
    call = models.McpToolCall
    recent = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    rows = (
        await db.execute(
            select(
                call.client,
                func.max(call.created_at).label("last_used_at"),
                func.sum(case((call.created_at >= recent, 1), else_=0)).label(
                    "recent_calls"
                ),
            )
            .where(call.user_id == user.id, call.created_at >= _retention_cutoff())
            .group_by(call.client)
            .order_by(func.max(call.created_at).desc())
        )
    ).all()

    token_ids = [
        int(client.removeprefix(PAT_CLIENT_PREFIX))
        for client, *_ in rows
        if client
        and client.startswith(PAT_CLIENT_PREFIX)
        and client.removeprefix(PAT_CLIENT_PREFIX).isdigit()
    ]
    tokens = {}
    if token_ids:
        result = await db.execute(
            select(models.ApiToken).where(
                models.ApiToken.owner_id == user.id,
                models.ApiToken.id.in_(token_ids),
            )
        )
        tokens = {token.id: token for token in result.scalars()}

    now = datetime.now(timezone.utc)
    connections = []
    for client, last_used_at, recent_calls in rows:
        if client and client.startswith(PAT_CLIENT_PREFIX):
            token_id = client.removeprefix(PAT_CLIENT_PREFIX)
            token = tokens.get(int(token_id)) if token_id.isdigit() else None
            if token is None:
                continue  # another user's id can't appear, but never leak one
            connections.append(
                Connection(
                    client=client,
                    kind="token",
                    last_used_at=last_used_at,
                    calls_last_30_days=int(recent_calls or 0),
                    token_id=token.id,
                    token_name=token.name,
                    token_active=token.revoked_at is None
                    and (token.expires_at is None or token.expires_at > now),
                )
            )
        else:
            connections.append(
                Connection(
                    client=client,
                    kind="oauth",
                    last_used_at=last_used_at,
                    calls_last_30_days=int(recent_calls or 0),
                )
            )
    return connections
