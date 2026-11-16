"""Data export tools: start the user's Excel export and check on it.

The export itself runs in ``export_worker``; these tools only queue it and
report on it (``services.data_exports``). The file is never handed to the AI:
a ready export is downloaded from the app's export page, where the user is
signed in, so the workbook (which holds more than any read tool returns, e.g.
the email) never passes through the model's context or a read-only token.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.ai.results import Handler, Outcome, ToolContext, ok
from app.core.config import settings
from app.models.data_export import DataExportStatus
from app.services import data_exports as export_service

# The web app's export page (wallet_v2 routes/_auth.settings.export.tsx).
EXPORT_PAGE_PATH = "/settings/export"
EXPORT_PAGE_NAME = "Settings > Export data"


def export_page() -> dict[str, Any]:
    """Where the user downloads the file: a URL when the app's is configured."""
    page: dict[str, Any] = {"name": EXPORT_PAGE_NAME}
    if settings.WEB_APP_URL:
        page["url"] = str(settings.WEB_APP_URL).rstrip("/") + EXPORT_PAGE_PATH
    return page


async def _export_data(
    _args, db: AsyncSession, user: models.User, _ctx: ToolContext
) -> Outcome:
    row, created = await export_service.start_export(db, user)
    return ok(
        {
            "export": export_service.status_of(row),
            "already_running": not created,
            "download_page": export_page(),
            "next_step": (
                "The file is built in the background, usually within a few "
                "minutes. Call get_export_status to check on it; the user "
                "downloads it from download_page in the app."
            ),
        }
    ), "export_started"


async def _export_status(
    _args, db: AsyncSession, user: models.User, _ctx: ToolContext
) -> Outcome:
    row = await export_service.latest_export(db, user)
    return ok(
        {"export": export_service.status_of(row), "download_page": export_page()}
    ), "export_status_fetched"


# -- approval ---------------------------------------------------------------------

ENTITY_TYPE = "data_export"


async def _preview_export(
    db: AsyncSession, user: models.User, tool_name: str, _args: dict
) -> dict:
    """What starting an export does: which earlier file it replaces."""
    inflight = await crud.data_export.get_inflight(db, owner_id=user.id)
    latest = await crud.data_export.get_latest_for_owner(db, owner_id=user.id)
    replaces = (
        latest.filename
        if latest is not None and latest.status == DataExportStatus.ready
        else None
    )
    return {
        "kind": tool_name,
        "operation": "export",
        "entity_type": ENTITY_TYPE,
        "already_running": inflight is not None,
        "replaces": replaces,
    }


EXPORT_PREVIEW_BUILDERS = {"export_data": _preview_export}

_MESSAGES = {
    "en": {
        "start": "Approve exporting all your data to an Excel file.",
        "replaces": " It replaces your previous export ({name}).",
        "running": "An export is already being prepared; approving shows its status.",
    },
    "es": {
        "start": "Aprueba exportar todos tus datos a un archivo de Excel.",
        "replaces": " Reemplaza tu exportación anterior ({name}).",
        "running": "Ya se está preparando una exportación; al aprobar verás su estado.",
    },
}


def format_export_message(preview: dict[str, Any], language: str) -> str:
    messages = _MESSAGES["es" if language == "es" else "en"]
    if preview.get("already_running"):
        return messages["running"]
    text = messages["start"]
    if preview.get("replaces"):
        text += messages["replaces"].format(name=preview["replaces"])
    return text


EXPORT_HANDLERS: dict[str, Handler] = {
    "export_data": _export_data,
    "get_export_status": _export_status,
}
