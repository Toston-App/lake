from datetime import datetime

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Date, and_, asc, cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app import crud
from app.crud.base import CRUDBase
from app.models.transfer import Transfer
from app.schemas.transfer import TransferCreate, TransferUpdate


class CRUDTransfer(CRUDBase[Transfer, TransferCreate, TransferUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: TransferCreate, owner_id: int
    ) -> Transfer:
        obj_in_data = jsonable_encoder(obj_in)

        # Convert date string to datetime object
        date_str = obj_in_data["date"]
        if date_str:
            try:
                obj_in_data["date"] = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                obj_in_data["date"] = None

        update_from = await crud.account.update_by_id_and_field(
            db=db,
            id=obj_in_data["from_acc"],
            column="total_transfers_out",
            amount=obj_in_data["amount"],
        )

        if update_from is None:
            return None

        update_to = await crud.account.update_by_id_and_field(
            db=db,
            id=obj_in_data["to_acc"],
            column="total_transfers_in",
            amount=obj_in_data["amount"],
        )

        if update_to is None:
            return None

        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_multi_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Transfer]:
        result = await db.execute(
            select(self.model)
            .filter(Transfer.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_multi_by_date(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        start_date: Date = None,
        end_date: str = None,
    ) -> list[Transfer]:
        query = select(self.model)

        query = query.where(
            and_(
                self.model.owner_id == owner_id,
                cast(self.model.date, Date) >= start_date,
                cast(self.model.date, Date) <= end_date,
            )
        ).order_by(asc(self.model.date))

        result = await db.execute(query)

        return result.scalars().all()


transfer = CRUDTransfer(Transfer)
