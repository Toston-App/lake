from typing import List

from fastapi.encoders import jsonable_encoder
# from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.imports import Import
from app.schemas.imports import ImportCreate, ImportUpdate


class CRUDImport(CRUDBase[Import, ImportCreate, ImportUpdate]):
    async def create_with_owner(
            self, db: AsyncSession, *, obj_in: ImportCreate, owner_id: int
    ) -> Import:
        obj_in_data = jsonable_encoder(obj_in)
        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_multi_by_owner(
            self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> List[Import]:
        result = await db.execute(
            select(self.model)
            .filter(Import.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
            .all()
        )
        return result.scalars().all()


imports = CRUDImport(Import)
