from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.subcategory import Subcategory
from app.schemas.subcategory import SubcategoryCreate, SubcategoryUpdate


class CRUDSubcategory(CRUDBase[Subcategory, SubcategoryCreate, SubcategoryUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: SubcategoryCreate, owner_id: int
    ) -> Subcategory:
        obj_in_data = jsonable_encoder(obj_in)
        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_multi_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Subcategory]:
        result = await db.execute(
            select(self.model)
            .filter(Subcategory.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()


subcategory = CRUDSubcategory(Subcategory)
