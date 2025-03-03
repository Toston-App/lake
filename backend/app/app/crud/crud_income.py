from typing import List
from datetime import datetime

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select
from sqlalchemy import and_, cast, Date, asc

from app.crud.base import CRUDBase
from app.models.income import Income
from app.schemas.income import IncomeCreate, IncomeUpdate
from app import crud


class CRUDIncome(CRUDBase[Income, IncomeCreate, IncomeUpdate]):
    async def create_with_owner(
            self, db: AsyncSession, *, obj_in: IncomeCreate, owner_id: int
    ) -> Income:
        obj_in_data = jsonable_encoder(obj_in)

        # Convert date string to datetime object
        date_str = obj_in_data['date']
        if date_str:
            try:
                obj_in_data['date'] = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                obj_in_data['date'] = None

        # Update account balance
        if obj_in_data['account_id']:
            update = await crud.account.update_by_id_and_field(db=db, id=obj_in_data['account_id'], column='total_incomes', amount=obj_in_data['amount'])

            if update == None:
                obj_in_data['account_id'] = None

        if obj_in_data['subcategory_id']:
            subcategory = await crud.subcategory.get(db=db, id=obj_in_data['subcategory_id'])

            if not subcategory:
                obj_in_data['subcategory_id'] = None

        if obj_in_data['place_id']:
            place = await crud.place.get(db=db, id=obj_in_data['place_id'])

            if not place:
                obj_in_data['place_id'] = None

        # Update User balance and total incomes
        await crud.user.update_balance(db=db, user_id=owner_id, is_Expense=False, amount=obj_in_data['amount'])

        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    # refactor this to only update balance once and not for each income
    async def create_multi_with_owner(
        self, db: AsyncSession, *, obj_list: List[IncomeCreate], owner_id: int
    ) -> List[Income]:
        created_incomes = []
        for obj_in in obj_list:
            income = await self.create_with_owner(db, obj_in=obj_in, owner_id=owner_id)
            created_incomes.append(income)
        return created_incomes

    async def remove_multi(
        self, db: AsyncSession, *, ids: List[int]
    ) -> List[Income]:
        removed_incomes = []
        for id in ids:
            income = await self.remove(db, id=id)
            if income:
                removed_incomes.append(income)
        return removed_incomes

    async def get_multi_by_owner(
            self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> List[Income]:
        result = await db.execute(
            select(self.model)
            .filter(Income.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_multi_by_date(
            self, db: AsyncSession, *, owner_id: int, start_date: Date = None, end_date: str = None
    ) -> List[Income]:
        query = select(self.model)

        query = query.where(
            and_(
                self.model.owner_id == owner_id,
                cast(self.model.date, Date) >= start_date,
                cast(self.model.date, Date) <= end_date
            )
        ).order_by(asc(self.model.date))

        result = await db.execute(query)

        return result.scalars().all()



income = CRUDIncome(Income)
