from datetime import datetime
from typing import Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Date, and_, asc, cast
from sqlalchemy import update as updateDb
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app import crud
from app.crud.base import CRUDBase
from app.models.income import Income
from app.schemas.income import IncomeCreate, IncomeUpdate


class CRUDIncome(CRUDBase[Income, IncomeCreate, IncomeUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: IncomeCreate, owner_id: int
    ) -> Income:
        obj_in_data = jsonable_encoder(obj_in)

        # Convert date string to datetime object
        date_str = obj_in_data["date"]
        if date_str:
            try:
                obj_in_data["date"] = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                obj_in_data["date"] = None

        # Update account balance
        if obj_in_data["account_id"]:
            update = await crud.account.update_by_id_and_field(
                db=db,
                owner_id=owner_id,
                id=obj_in_data["account_id"],
                column="total_incomes",
                amount=obj_in_data["amount"],
            )

            if update == None:
                obj_in_data["account_id"] = None

        if obj_in_data["subcategory_id"]:
            subcategory = await crud.subcategory.get(
                db=db, id=obj_in_data["subcategory_id"]
            )

            if not subcategory or subcategory.owner_id != owner_id:
                obj_in_data["subcategory_id"] = None
            else:
                # Update subcategory total
                await db.execute(
                    updateDb(subcategory.__class__)
                    .where(subcategory.__class__.id == subcategory.id)
                    .values(total=subcategory.total + obj_in_data["amount"])
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

                # Update category total if there's a category associated with the subcategory
                if subcategory.category_id:
                    category = await crud.category.get(db=db, id=subcategory.category_id)

                    if category:
                        await db.execute(
                            updateDb(category.__class__)
                            .where(category.__class__.id == category.id)
                            .values(total=category.total + obj_in_data["amount"])
                            .execution_options(synchronize_session="fetch")
                        )
                        await db.commit()

        if obj_in_data["place_id"]:
            place = await crud.place.get(db=db, id=obj_in_data["place_id"])

            if not place:
                obj_in_data["place_id"] = None

        # Update User balance and total incomes
        await crud.user.update_balance(
            db=db, user_id=owner_id, is_Expense=False, amount=obj_in_data["amount"]
        )

        # Update goal progress if goal_id is provided
        if obj_in_data.get("goal_id"):
            goal = await crud.goal.get(db=db, id=obj_in_data["goal_id"])
            if goal and goal.owner_id == owner_id:
                await crud.goal.update_goal_amount(
                    db=db, goal_id=obj_in_data["goal_id"], amount=obj_in_data["amount"], is_positive=True
                )
            else:
                obj_in_data["goal_id"] = None


        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    # refactor this to only update balance once and not for each income
    async def create_multi_with_owner(
        self, db: AsyncSession, *, obj_list: list[IncomeCreate], owner_id: int
    ) -> list[Income]:
        created_incomes = []
        for obj_in in obj_list:
            income = await self.create_with_owner(db, obj_in=obj_in, owner_id=owner_id)
            created_incomes.append(income)
        return created_incomes

    async def remove_multi(self, db: AsyncSession, *, ids: list[int]) -> list[Income]:
        removed_incomes = []
        for id in ids:
            income = await self.remove(db, id=id)
            if income:
                removed_incomes.append(income)
        return removed_incomes

    async def get_multi_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Income]:
        result = await db.execute(
            select(self.model)
            .filter(Income.owner_id == owner_id)
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
    ) -> list[Income]:
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

    async def update_with_owner(
        self, db: AsyncSession, *, db_obj: Income, obj_in: IncomeUpdate, owner_id: int
    ) -> Income:
        if db_obj.owner_id != owner_id:
            return None

        old_amount = db_obj.amount
        old_goal_id = db_obj.goal_id
        old_account_id = db_obj.account_id
        old_subcategory_id = db_obj.subcategory_id

        obj_in_data = jsonable_encoder(obj_in)

        # Convert date string to datetime object
        if "date" in obj_in_data and obj_in_data["date"]:
            try:
                obj_in_data["date"] = datetime.strptime(obj_in_data["date"], "%Y-%m-%d").date()
            except:
                obj_in_data["date"] = None

        # Update object fields
        for field, value in obj_in_data.items():
            if value is not None:
                setattr(db_obj, field, value)

        new_amount = db_obj.amount
        new_goal_id = db_obj.goal_id
        new_account_id = db_obj.account_id
        new_subcategory_id = db_obj.subcategory_id

        # Handle account balance changes
        if old_account_id != new_account_id or old_amount != new_amount:
            if old_account_id:
                # Remove old amount from old account
                await crud.account.update_by_id_and_field(
                    db=db,
                    owner_id=owner_id,
                    id=old_account_id,
                    column="total_incomes",
                    amount=-old_amount,
                )

            if new_account_id:
                # Add new amount to new account
                account_update = await crud.account.update_by_id_and_field(
                    db=db,
                    owner_id=owner_id,
                    id=new_account_id,
                    column="total_incomes",
                    amount=new_amount,
                )
                if account_update is None:
                    db_obj.account_id = None

        # Handle subcategory changes
        if old_subcategory_id != new_subcategory_id or old_amount != new_amount:
            if old_subcategory_id:
                # Remove old amount from old subcategory
                old_subcategory = await crud.subcategory.get(db=db, id=old_subcategory_id)
                if old_subcategory:
                    await db.execute(
                        updateDb(old_subcategory.__class__)
                        .where(old_subcategory.__class__.id == old_subcategory.id)
                        .values(total=old_subcategory.total - old_amount)
                        .execution_options(synchronize_session="fetch")
                    )
                    await db.commit()

                    # Update category total
                    if old_subcategory.category_id:
                        category = await crud.category.get(db=db, id=old_subcategory.category_id)
                        if category:
                            await db.execute(
                                updateDb(category.__class__)
                                .where(category.__class__.id == category.id)
                                .values(total=category.total - old_amount)
                                .execution_options(synchronize_session="fetch")
                            )
                            await db.commit()

            if new_subcategory_id:
                # Add new amount to new subcategory
                new_subcategory = await crud.subcategory.get(db=db, id=new_subcategory_id)
                if new_subcategory and new_subcategory.owner_id == owner_id:
                    await db.execute(
                        updateDb(new_subcategory.__class__)
                        .where(new_subcategory.__class__.id == new_subcategory.id)
                        .values(total=new_subcategory.total + new_amount)
                        .execution_options(synchronize_session="fetch")
                    )
                    await db.commit()

                    # Update category total
                    if new_subcategory.category_id:
                        category = await crud.category.get(db=db, id=new_subcategory.category_id)
                        if category:
                            await db.execute(
                                updateDb(category.__class__)
                                .where(category.__class__.id == category.id)
                                .values(total=category.total + new_amount)
                                .execution_options(synchronize_session="fetch")
                            )
                            await db.commit()
                else:
                    db_obj.subcategory_id = None

        # Handle user balance changes
        if old_amount != new_amount:
            balance_diff = new_amount - old_amount
            await crud.user.update_balance(
                db=db, user_id=owner_id, is_Expense=False, amount=balance_diff
            )

        # Handle goal updates - recalculate affected goals
        goals_to_recalculate = set()
        if old_goal_id:
            goals_to_recalculate.add(old_goal_id)
        if new_goal_id:
            # Validate new goal ownership
            goal = await crud.goal.get(db=db, id=new_goal_id)
            if goal and goal.owner_id == owner_id:
                goals_to_recalculate.add(new_goal_id)
            else:
                db_obj.goal_id = None

        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)

        # Recalculate goal progress for affected goals
        for goal_id in goals_to_recalculate:
            await crud.goal.recalculate_goal_progress(db=db, goal_id=goal_id)

        return db_obj

    async def remove_with_owner(
        self, db: AsyncSession, *, income_id: int, owner_id: int
    ) -> Optional[Income]:
        result = await db.execute(
            select(self.model)
            .filter(and_(Income.owner_id == owner_id, Income.id == income_id))
        )
        db_obj = result.scalars().first()

        if not db_obj:
            return None

        # Store values before deletion for cleanup
        old_amount = db_obj.amount
        old_goal_id = db_obj.goal_id
        old_account_id = db_obj.account_id
        old_subcategory_id = db_obj.subcategory_id

        # Update account balance
        if old_account_id:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=owner_id,
                id=old_account_id,
                column="total_incomes",
                amount=-old_amount,
            )

        # Update subcategory totals
        if old_subcategory_id:
            subcategory = await crud.subcategory.get(db=db, id=old_subcategory_id)
            if subcategory:
                await db.execute(
                    updateDb(subcategory.__class__)
                    .where(subcategory.__class__.id == subcategory.id)
                    .values(total=subcategory.total - old_amount)
                    .execution_options(synchronize_session="fetch")
                )
                await db.commit()

                # Update category total
                if subcategory.category_id:
                    category = await crud.category.get(db=db, id=subcategory.category_id)
                    if category:
                        await db.execute(
                            updateDb(category.__class__)
                            .where(category.__class__.id == category.id)
                            .values(total=category.total - old_amount)
                            .execution_options(synchronize_session="fetch")
                        )
                        await db.commit()

        # Update user balance
        await crud.user.update_balance(
            db=db, user_id=owner_id, is_Expense=False, amount=-old_amount
        )

        # Delete the income
        await db.delete(db_obj)
        await db.commit()

        # Recalculate goal progress if it was linked to a goal
        if old_goal_id:
            await crud.goal.recalculate_goal_progress(db=db, goal_id=old_goal_id)

        return db_obj


income = CRUDIncome(Income)
