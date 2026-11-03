"""Generic CSV / XLSX import.

Flow:
1. ``POST /import/sessions``                upload a file, get headers + suggested mapping
2. ``PUT  /import/sessions/{id}/mapping``    confirm columns, get values to match
3. ``POST /import/sessions/{id}/preview``    dry run of the user's decisions
4. ``POST /import/sessions/{id}/commit``     import everything in one transaction
5. ``POST /import/{id}/undo``                remove an import and what it created
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api import deps
from app.services.imports import service
from app.services.imports.reader import MAX_FILE_BYTES
from app.utilities.redis import invalidate_user_cache, r
from app.utilities.wide_events import enrich_event, mark_for_logging

router = APIRouter()

UPLOADS_PER_HOUR = 20
# Multipart overhead on top of the file itself.
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 64 * 1024


async def enforce_import_rate_limit(user_id: int) -> None:
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = f"rate-limit:imports:{user_id}:{hour}"
    try:
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 3600)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Imports are temporarily unavailable"
        ) from exc
    if count > UPLOADS_PER_HOUR:
        raise HTTPException(
            status_code=429, detail="Too many imports, try again in an hour"
        )


async def _read_bounded(request: Request, upload: UploadFile) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 10 MB.")
    data = await upload.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 10 MB.")
    return data


@router.post("/sessions", response_model=schemas.SessionCreated)
async def create_session(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    file: UploadFile = File(...),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Upload a CSV or XLSX file and start an import session."""
    mark_for_logging(request)
    await enforce_import_rate_limit(current_user.id)
    data = await _read_bounded(request, file)
    session = await service.create_session(db, current_user, file.filename or "", data)
    enrich_event(
        request,
        user={"id": current_user.id},
        operation={
            "type": "import_upload",
            "file_type": session.file_type,
            "file_size": len(data),
            "rows": session.total_rows,
            "columns": len(session.headers),
            "profile_used": session.profile_id is not None,
        },
    )
    return session


@router.put("/sessions/{import_id}/mapping", response_model=schemas.MappingResult)
async def set_mapping(
    import_id: int,
    body: schemas.MappingIn,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Confirm which column holds each field and get the values to match."""
    draft = await service.get_draft(db, current_user.id, import_id)
    return await service.apply_mapping(db, current_user, draft, body)


@router.post("/sessions/{import_id}/preview", response_model=schemas.CommitPreview)
async def preview(
    import_id: int,
    decisions: schemas.Decisions,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Dry run: what would be imported, created and skipped."""
    draft = await service.get_draft(db, current_user.id, import_id)
    return await service.preview_commit(db, current_user, draft, decisions)


@router.post("/sessions/{import_id}/commit", response_model=schemas.CommitResult)
async def commit(
    request: Request,
    import_id: int,
    decisions: schemas.Decisions,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Import the file using the user's decisions, in a single transaction."""
    mark_for_logging(request)
    result = await service.commit_import(db, current_user, import_id, decisions)
    await invalidate_user_cache(current_user.id)
    enrich_event(
        request,
        user={"id": current_user.id},
        operation={"type": "import_commit", "import_id": result.import_id},
        import_results=result.model_dump(exclude={"message"}),
    )
    return result


@router.delete("/sessions/{import_id}", status_code=204)
async def cancel_session(
    import_id: int,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> None:
    """Discard an import session and its staged data."""
    await service.cancel_session(db, current_user, import_id)


@router.get("/profiles", response_model=list[schemas.ImportProfileOut])
async def list_profiles(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    return await service.list_profiles(db, current_user.id)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: int,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> None:
    await service.delete_profile(db, current_user.id, profile_id)


@router.get("", response_model=list[schemas.ImportSummary])
async def list_imports(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Import history."""
    return await service.list_imports(db, current_user.id)


@router.get("/{import_id}", response_model=schemas.ImportSummary)
async def get_import(
    import_id: int,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    return await service.get_import(db, current_user.id, import_id)


@router.post("/{import_id}/undo", response_model=schemas.ImportSummary)
async def undo(
    request: Request,
    import_id: int,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """Delete the transactions of an import and anything it created that is unused."""
    mark_for_logging(request)
    record = await service.undo_import(db, current_user, import_id)
    await invalidate_user_cache(current_user.id)
    enrich_event(
        request,
        user={"id": current_user.id},
        operation={"type": "import_undo", "import_id": import_id},
    )
    return record
