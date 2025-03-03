from typing import List

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.account import Account
from app.schemas.account import AccountCreate, AccountUpdate

class CRUDAccount(CRUDBase[Account, AccountCreate, AccountUpdate]):
    async def create_with_owner(
            self, db: AsyncSession, *, obj_in: AccountCreate, owner_id: int
    ) -> Account:
        obj_in_data = jsonable_encoder(obj_in)

        if(obj_in_data['initial_balance'] != None):
            obj_in_data['current_balance'] = obj_in_data['initial_balance']

        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_multi_by_owner(
            self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> List[Account]:
        result = await db.execute(
            select(self.model)
            .filter(Account.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_id(
            self, db: AsyncSession, *, id: int
    ) -> Account:
        result = await db.execute(
            select(self.model)
            .filter(Account.id == id)
        )
        return result.scalars().first()

    # TODO: Make and enum for columns
    async def update_by_id_and_field(self, db: AsyncSession, *, id: int, column: str, amount: float):
        account = await self.get_by_id(db=db, id=id)

        if not account:
            return None

        current_account_data = jsonable_encoder(account)

        account_in = AccountUpdate(**current_account_data)

        if column == 'total_expenses':
            account_in.current_balance -= amount
            account_in.total_expenses += amount

        if column == 'total_incomes':
            account_in.current_balance += amount
            account_in.total_incomes += amount

        if column == 'total_transfers_in':
            account_in.current_balance += amount
            account_in.total_transfers_in += amount

        if column == 'total_transfers_out':
            account_in.current_balance -= amount
            account_in.total_transfers_out += amount

        # TODO: check if this is needed
        # if column == 'initial_balance':
        #     account_in.initial_balance += amount
        #     account_in.current_balance += amount

        # if column ==  'current_balance':
        #     account_in.current_balance += amount


        await self.update(db=db, db_obj=account, obj_in=account_in)

        return account



account = CRUDAccount(Account)
