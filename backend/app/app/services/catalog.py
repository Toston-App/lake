"""Category, subcategory, and place services shared by API and chat."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import crud, models, schemas
from app.services.errors import DomainError, NotFoundError, PermissionDeniedError


async def list_categories(
    db: AsyncSession, user: models.User, *, skip: int = 0, limit: int = 100
):
    if crud.user.is_superuser(user):
        return await crud.category.get_multi(db, skip=skip, limit=limit)
    return await crud.category.get_multi_by_owner(
        db=db, owner_id=user.id, skip=skip, limit=limit
    )


async def get_category(db: AsyncSession, user: models.User, category_id: int):
    result = await db.execute(
        select(models.Category)
        .options(selectinload(models.Category.subcategories))
        .filter(models.Category.id == category_id)
    )
    category = result.scalar_one_or_none()
    if not category:
        raise NotFoundError("Category not found")
    if not crud.user.is_superuser(user) and category.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return category


async def create_category(
    db: AsyncSession, user: models.User, category_in: schemas.CategoryCreate
):
    return await crud.category.create_with_owner(
        db=db, obj_in=category_in, owner_id=user.id
    )


async def update_category(
    db: AsyncSession,
    user: models.User,
    category_id: int,
    category_in: schemas.CategoryUpdate,
):
    category = await get_category(db, user, category_id)
    category_in.updated_at = datetime.now(timezone.utc)
    await crud.category.update(db=db, db_obj=category, obj_in=category_in)
    # CRUDBase.update() commits and refreshes the scalar fields, which expires
    # relationships. Re-fetch with get_category() so response serialization
    # never attempts the Category.subcategories lazy="raise_on_sql" loader.
    return await get_category(db, user, category_id)


async def delete_category(db: AsyncSession, user: models.User, category_id: int):
    category = await get_category(db, user, category_id)
    if category.is_income:
        raise DomainError("This item cannot be deleted")
    await crud.category.remove(db=db, id=category_id)


async def list_subcategories(
    db: AsyncSession, user: models.User, *, skip: int = 0, limit: int = 100
):
    if crud.user.is_superuser(user):
        return await crud.subcategory.get_multi(db, skip=skip, limit=limit)
    return await crud.subcategory.get_multi_by_owner(
        db=db, owner_id=user.id, skip=skip, limit=limit
    )


async def get_subcategory(db: AsyncSession, user: models.User, subcategory_id: int):
    subcategory = await crud.subcategory.get(db=db, id=subcategory_id)
    if not subcategory:
        raise NotFoundError("Subcategory not found")
    if not crud.user.is_superuser(user) and subcategory.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return subcategory


async def create_subcategory(
    db: AsyncSession, user: models.User, subcategory_in: schemas.SubcategoryCreate
):
    category = await crud.category.get(db=db, id=subcategory_in.category_id)
    if not category or (
        not crud.user.is_superuser(user) and category.owner_id != user.id
    ):
        raise NotFoundError("Parent category not found")
    return await crud.subcategory.create_with_owner(
        db=db, obj_in=subcategory_in, owner_id=user.id
    )


async def update_subcategory(
    db: AsyncSession,
    user: models.User,
    subcategory_id: int,
    subcategory_in: schemas.SubcategoryUpdate,
):
    subcategory = await get_subcategory(db, user, subcategory_id)
    subcategory_in.updated_at = datetime.now(timezone.utc)
    return await crud.subcategory.update(
        db=db, db_obj=subcategory, obj_in=subcategory_in
    )


async def delete_subcategory(
    db: AsyncSession, user: models.User, subcategory_id: int
):
    await get_subcategory(db, user, subcategory_id)
    await crud.subcategory.remove(db=db, id=subcategory_id)


async def list_places(
    db: AsyncSession, user: models.User, *, skip: int = 0, limit: int = 100
):
    if crud.user.is_superuser(user):
        return await crud.place.get_multi(db, skip=skip, limit=limit)
    return await crud.place.get_multi_by_owner(
        db=db, owner_id=user.id, skip=skip, limit=limit
    )


async def get_place(db: AsyncSession, user: models.User, place_id: int):
    place = await crud.place.get(db=db, id=place_id)
    if not place:
        raise NotFoundError("Place not found")
    if not crud.user.is_superuser(user) and place.owner_id != user.id:
        raise PermissionDeniedError("Not enough permissions")
    return place


async def create_place(
    db: AsyncSession, user: models.User, place_in: schemas.PlaceCreate
):
    return await crud.place.create_with_owner(db=db, obj_in=place_in, owner_id=user.id)


async def update_place(
    db: AsyncSession,
    user: models.User,
    place_id: int,
    place_in: schemas.PlaceUpdate,
):
    place = await get_place(db, user, place_id)
    place_in.updated_at = datetime.now(timezone.utc)
    return await crud.place.update(db=db, db_obj=place, obj_in=place_in)


async def delete_place(db: AsyncSession, user: models.User, place_id: int):
    await get_place(db, user, place_id)
    await crud.place.remove(db=db, id=place_id)
