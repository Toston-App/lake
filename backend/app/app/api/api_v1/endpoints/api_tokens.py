"""Personal access tokens for MCP clients that can only send a static header.

These endpoints need a normal signed-in session: ``deps.get_current_user``
doesn't accept a ``tsk_`` token, so a token can't mint or list tokens.
"""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.core.config import settings
from app.core.tiers import TIERS
from app.services import api_tokens as api_token_service
from app.services import mcp_audit
from app.services.entitlements import get_user_tier
from app.services.errors import PermissionDeniedError
from app.utilities.wide_events import enrich_event

router = APIRouter()


def _enrich(request: Request, user: models.User, operation: dict) -> None:
    # Never put the token itself in the event.
    enrich_event(
        request, user={"id": user.id, "email": user.email}, operation=operation
    )


@router.post(
    "",
    response_model=schemas.ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_token(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    token_in: schemas.ApiTokenCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> schemas.ApiTokenCreated:
    """
    Create a token for the MCP endpoint. The response is the only time the
    token is shown; store it like a password.
    """
    _enrich(
        request,
        current_user,
        {"type": "api_token_create", "scope": token_in.scope},
    )
    if not _mcp_enabled():
        # A token only works on /mcp; don't mint ones that can't be used.
        raise PermissionDeniedError("AI connections are not available")
    token, plaintext = await api_token_service.create_token(
        db,
        current_user,
        token_in.name,
        token_in.scope,
        token_in.expires_in_days,
    )
    return schemas.ApiTokenCreated(
        **schemas.ApiToken.model_validate(token).model_dump(), token=plaintext
    )


@router.get("", response_model=list[schemas.ApiToken])
async def list_api_tokens(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> list[models.ApiToken]:
    """
    The user's tokens that still work, newest first.
    """
    _enrich(request, current_user, {"type": "api_token_list"})
    return await api_token_service.list_tokens(db, current_user)


def _mcp_enabled() -> bool:
    return settings.MCP_ENABLED and bool(settings.MCP_PUBLIC_URL)


async def _connections(
    db: AsyncSession, user: models.User
) -> schemas.AiConnections:
    tier, _ = await get_user_tier(db, user.id)
    connections = await mcp_audit.list_connections(db, user)
    return schemas.AiConnections(
        enabled=_mcp_enabled(),
        mcp_url=settings.MCP_PUBLIC_URL if _mcp_enabled() else None,
        read_only=user.mcp_read_only,
        calls_this_month=await mcp_audit.calls_this_month(db, user.id),
        monthly_call_limit=TIERS[tier].mcp_monthly_call_limit,
        connections=[schemas.AiConnection(**vars(c)) for c in connections],
    )


@router.get("/connections", response_model=schemas.AiConnections)
async def list_ai_connections(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> schemas.AiConnections:
    """
    The MCP endpoint's URL, the user's AI settings and monthly usage, and the
    AI clients that used it recently: OAuth connections (e.g. Claude,
    ChatGPT) and personal access tokens.
    """
    _enrich(request, current_user, {"type": "ai_connections_list"})
    return await _connections(db, current_user)


@router.put("/connections/settings", response_model=schemas.AiConnections)
async def update_ai_connection_settings(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    settings_in: schemas.AiConnectionSettings,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> schemas.AiConnections:
    """
    With `read_only`, every AI app (OAuth or token) may only read data.
    """
    _enrich(
        request,
        current_user,
        {"type": "ai_connections_settings", "read_only": settings_in.read_only},
    )
    current_user.mcp_read_only = settings_in.read_only
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return await _connections(db, current_user)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    token_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Response:
    """
    Revoke a token. It stops working on the next request.
    """
    _enrich(request, current_user, {"type": "api_token_revoke", "token_id": token_id})
    await api_token_service.revoke_token(db, current_user, token_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
