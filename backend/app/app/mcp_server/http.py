"""HTTP surface of the MCP server: the /mcp ASGI app and OAuth discovery routes.

The transport is stateless (no sticky sessions behind several workers) and
answers with plain JSON instead of SSE.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from app.core.config import settings
from app.mcp_server.auth import BearerAuthMiddleware
from app.mcp_server.server import server

_manager: StreamableHTTPSessionManager | None = None


def new_session_manager() -> StreamableHTTPSessionManager:
    """Create (and remember) a fresh manager. `.run()` may be entered only once per instance."""
    global _manager
    _manager = StreamableHTTPSessionManager(
        app=server, stateless=True, json_response=True
    )
    return _manager


async def _mcp_asgi(scope, receive, send) -> None:
    if _manager is None:
        # Only between process start and the startup event.
        response = JSONResponse({"detail": "MCP is starting"}, status_code=503)
        await response(scope, receive, send)
    else:
        await _manager.handle_request(scope, receive, send)


mcp_asgi_app = BearerAuthMiddleware(_mcp_asgi)

well_known_router = APIRouter()

_CORS = {"Access-Control-Allow-Origin": "*"}
_AS_TTL_SECONDS = 3600
_as_cache: tuple[float, dict[str, Any]] | None = None


def _not_found() -> Response:
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _mcp_available() -> bool:
    return bool(settings.MCP_ENABLED and settings.MCP_PUBLIC_URL)


async def _fetch_authorization_server_metadata() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(
            f"{settings.CLERK_ISSUER}/.well-known/oauth-authorization-server"
        )
        response.raise_for_status()
        return response.json()


def _protected_resource_response() -> Response:
    if not _mcp_available():
        return _not_found()
    body = {
        "resource": settings.MCP_PUBLIC_URL,
        "authorization_servers": [settings.CLERK_ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["openid", "profile", "email"],
        "resource_name": "Toston",
    }
    return JSONResponse(body, headers=_CORS)


@well_known_router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def oauth_protected_resource() -> Response:
    # Root fallback for clients that do not insert the resource path.
    return _protected_resource_response()


@well_known_router.get(
    "/.well-known/oauth-protected-resource/{resource_path:path}",
    include_in_schema=False,
)
async def oauth_protected_resource_for_path(resource_path: str) -> Response:
    # RFC 9728 section 3.1: the metadata path is derived from MCP_PUBLIC_URL,
    # and only that exact resource is described.
    expected = urlsplit(settings.MCP_PUBLIC_URL or "").path.strip("/")
    if resource_path.strip("/") != expected:
        return _not_found()
    return _protected_resource_response()


@well_known_router.get(
    "/.well-known/oauth-authorization-server", include_in_schema=False
)
async def oauth_authorization_server() -> Response:
    global _as_cache
    if not _mcp_available():
        return _not_found()
    now = time.monotonic()
    if _as_cache is None or now - _as_cache[0] >= _AS_TTL_SECONDS:
        try:
            data = await _fetch_authorization_server_metadata()
        except Exception:  # noqa: BLE001
            return JSONResponse(
                {"detail": "Authorization server unavailable"},
                status_code=502,
                headers=_CORS,
            )
        _as_cache = (now, data)
    return JSONResponse(_as_cache[1], headers=_CORS)
