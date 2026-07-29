from decimal import Decimal

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app import crud
from app.crud.base import CRUDBase
from app.models.account import Account
from app.schemas.account import AccountCreate, AccountUpdate
from app.schemas.user import UserUpdate


class CRUDAccount(CRUDBase[Account, AccountCreate, AccountUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: AccountCreate, owner_id: int
    ) -> Account:
        obj_in_data = jsonable_encoder(obj_in)

        if obj_in_data["initial_balance"] is not None:
            obj_in_data["current_balance"] = obj_in_data["initial_balance"]

        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)

        # Update user's balance_total if there's an initial balance
        if obj_in_data.get("initial_balance") and obj_in_data["initial_balance"] != 0:
            user = await crud.user.get(db, id=owner_id)
            user_data = jsonable_encoder(user)
            user_in = UserUpdate(**user_data)
            user_in.balance_total = user.balance_total + obj_in_data["initial_balance"]
            await crud.user.update(db, db_obj=user, obj_in=user_in)

        return db_obj

    async def get_multi_by_owner(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        skip: int = 0,
        limit: int | None = 100,
    ) -> list[Account]:
        query = (
            select(self.model)
            .filter(Account.owner_id == owner_id)
            .order_by(Account.name)
            .offset(skip)
        )
        if limit is not None:
            query = query.limit(limit)
        result = await db.execute(query)
        return result.scalars().all()

    async def get_by_id(self, db: AsyncSession, *, owner_id: int, id: int) -> Account:
        result = await db.execute(
            select(self.model).filter(Account.id == id, Account.owner_id == owner_id)
        )
        return result.scalars().first()

    async def get_for_update_by_id(
        self, db: AsyncSession, *, owner_id: int, id: int
    ) -> Account | None:
        result = await db.execute(
            select(self.model)
            .filter(Account.id == id, Account.owner_id == owner_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalars().first()

    # TODO: Make and enum for columns
    async def update_by_id_and_field(
        self, db: AsyncSession, *, owner_id: int, id: int, column: str, amount: float
    ):
        account = await self.get_by_id(db=db, owner_id=owner_id, id=id)

        if not account:
            return None

        current_account_data = jsonable_encoder(account)

        account_in = AccountUpdate(**current_account_data)

        if column == "total_expenses":
            account_in.current_balance -= amount
            account_in.total_expenses += amount

        if column == "total_incomes":
            account_in.current_balance += amount
            account_in.total_incomes += amount

        if column == "total_transfers_in":
            account_in.current_balance += amount
            account_in.total_transfers_in += amount

        if column == "total_transfers_out":
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

    async def recalculate_total_investments(
        self, db: AsyncSession, *, account_id: int, commit: bool = True
    ) -> Account:
        """Recalculate total_investments for an account based on its holdings."""
        from sqlalchemy import func

        from app.models.holding import Holding

        account_result = await db.execute(
            select(Account)
            .filter(Account.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        account = account_result.scalars().first()
        if account:
            totals = await db.execute(
                select(
                    func.sum(Holding.current_value_usd),
                    func.sum(Holding.current_value_mxn),
                ).filter(Holding.account_id == account_id)
            )
            total_usd, total_mxn = totals.one()
            account.total_investments_usd = total_usd or Decimal("0")
            account.total_investments_mxn = total_mxn or Decimal("0")
            db.add(account)
            if commit:
                await db.commit()
            else:
                await db.flush()
            await db.refresh(account)
        return account


account = CRUDAccount(Account)
