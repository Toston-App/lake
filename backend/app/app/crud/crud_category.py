from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryUpdate


class CRUDCategory(CRUDBase[Category, CategoryCreate, CategoryUpdate]):
    async def get(self, db: AsyncSession, owner_id: int, id: int) -> Category | None:
        result = await db.execute(
            select(self.model)
            .filter(Category.owner_id == owner_id, Category.id == id)
            .options(selectinload(self.model.subcategories))
        )
        return result.scalars().first()

    async def get_multi(
        self, db: AsyncSession, *, skip: int = 0, limit: int = 100
    ) -> list[Category]:
        result = await db.execute(
            select(self.model)
            .order_by(Category.name)
            .options(selectinload(self.model.subcategories))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: CategoryCreate, owner_id: int
    ) -> Category:
        obj_in_data = jsonable_encoder(obj_in)
        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        # Eagerly load subcategories after creation
        result = await db.execute(
            select(self.model)
            .filter_by(id=db_obj.id)
            .options(selectinload(self.model.subcategories))
        )
        return result.scalars().one()

    async def get_multi_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Category]:
        result = await db.execute(
            select(self.model)
            .filter(Category.owner_id == owner_id)
            .order_by(Category.name)
            .options(selectinload(self.model.subcategories))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()


category = CRUDCategory(Category)
