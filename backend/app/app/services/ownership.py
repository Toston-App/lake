"""Ownership checks for entities a user references by id.

Superusers may use anyone's entities unless a caller opts out.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.services.errors import NotFoundError, PermissionDeniedError


def owns(user: models.User, obj: Any) -> bool:
    return crud.user.is_superuser(user) or obj.owner_id == user.id


async def get_owned(
    db: AsyncSession, user: models.User, crud_obj: Any, obj_id: int | None
) -> Any | None:
    """The entity when it exists and the user may use it; None otherwise."""
    if obj_id is None:
        return None
    obj = await crud_obj.get(db, obj_id)
    return obj if obj and owns(user, obj) else None


async def owned_id(
    db: AsyncSession, user: models.User, crud_obj: Any, obj_id: int | None
) -> int | None:
    """`obj_id` when the user may use it; None otherwise, so an optional
    reference that is missing or someone else's is silently dropped."""
    if not obj_id:
        return None
    return obj_id if await get_owned(db, user, crud_obj, obj_id) else None


async def require_owned(
    db: AsyncSession,
    user: models.User,
    crud_obj: Any,
    obj_id: int | None,
    label: str,
    *,
    hide_foreign: bool,
    allow_superuser: bool = True,
) -> Any | None:
    """Fetch a referenced entity, failing when it is missing or not the user's.

    An absent reference (`obj_id` None) passes and returns None.
    With `hide_foreign`, another user's entity is reported as missing (404), so
    ids can't be probed; otherwise it is a permission error (403).
    """
    if obj_id is None:
        return None
    obj = await crud_obj.get(db, obj_id)
    if not obj:
        raise NotFoundError(f"{label} not found")
    if obj.owner_id != user.id and not (allow_superuser and owns(user, obj)):
        if hide_foreign:
            raise NotFoundError(f"{label} not found")
        raise PermissionDeniedError("Not enough permissions")
    return obj
