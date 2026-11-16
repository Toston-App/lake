"""Names for the ids a confirmation preview shows, never revealing other users' records.

A leaf module: ``confirmation`` and the feature tool modules both import it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models


async def label_account(
    db: AsyncSession, user: models.User, account_id: int | None
) -> str | None:
    if account_id is None:
        return None
    account = await crud.account.get_by_id(db, owner_id=user.id, id=account_id)
    return account.name if account else f"Account #{account_id}"


def owned_name(user: models.User, record: Any) -> str | None:
    """Name of ``record`` if it belongs to ``user`` (the services' ownership rule)."""
    if record is None or (
        record.owner_id != user.id and not crud.user.is_superuser(user)
    ):
        return None
    return record.name


async def label_category(
    db: AsyncSession, user: models.User, category_id: int | None
) -> str | None:
    if category_id is None:
        return None
    category = await crud.category.get(db=db, id=category_id)
    return owned_name(user, category) or f"Category #{category_id}"


async def label_subcategory(
    db: AsyncSession, user: models.User, subcategory_id: int | None
) -> str | None:
    if subcategory_id is None:
        return None
    sub = await crud.subcategory.get(db=db, id=subcategory_id)
    return owned_name(user, sub) or f"Subcategory #{subcategory_id}"


async def label_place(
    db: AsyncSession, user: models.User, place_id: int | None
) -> str | None:
    if place_id is None:
        return None
    place = await crud.place.get(db=db, id=place_id)
    return owned_name(user, place) or f"Place #{place_id}"


Labeler = Callable[[AsyncSession, models.User, Any], Awaitable[str | None]]

# Each id field a preview may show, and how to name it.
LABELERS: dict[str, Labeler] = {
    "account_id": label_account,
    "to_account_id": label_account,
    "from_acc": label_account,
    "to_acc": label_account,
    "category_id": label_category,
    "subcategory_id": label_subcategory,
    "place_id": label_place,
}

EXPENSE_FIELDS = ("account_id", "category_id", "subcategory_id", "place_id")
INCOME_FIELDS = ("account_id", "subcategory_id", "place_id")
TRANSFER_FIELDS = ("from_acc", "to_acc")
RECURRING_FIELDS = (
    "account_id",
    "to_account_id",
    "category_id",
    "subcategory_id",
    "place_id",
)


async def labels_for(
    db: AsyncSession, user: models.User, fields: dict, keys: Iterable[str]
) -> dict[str, str]:
    """Names for the ``keys`` that ``fields`` sets; unset ones are left out."""
    labels: dict[str, str] = {}
    for key in keys:
        if fields.get(key) is not None:
            name = await LABELERS[key](db, user, fields[key])
            if name:
                labels[key] = name
    return labels
