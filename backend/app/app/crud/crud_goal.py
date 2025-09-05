from datetime import datetime

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Date, and_, asc, cast, desc
from sqlalchemy import update as updateDb
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app import crud
from app.crud.base import CRUDBase
from app.models.goal import Goal
from app.schemas.goal import GoalCreate, GoalUpdate


class CRUDGoal(CRUDBase[Goal, GoalCreate, GoalUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: GoalCreate, owner_id: int
    ) -> Goal:
        obj_in_data = jsonable_encoder(obj_in)

        # Convert target_date string to date object if provided
        target_date = obj_in_data.get("target_date")
        if target_date and isinstance(target_date, str):
            try:
                obj_in_data["target_date"] = datetime.strptime(target_date, "%Y-%m-%d").date()
            except:
                obj_in_data["target_date"] = None

        # Validate account ownership if account_id is provided
        if obj_in_data.get("account_id"):
            account = await crud.account.get(db=db, id=obj_in_data["account_id"])
            if not account or account.owner_id != owner_id:
                obj_in_data["account_id"] = None

        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_multi_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Goal]:
        result = await db.execute(
            select(self.model)
            .filter(Goal.owner_id == owner_id)
            .order_by(desc(Goal.created_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_active_goals_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Goal]:
        result = await db.execute(
            select(self.model)
            .filter(Goal.owner_id == owner_id, Goal.is_completed == False)
            .order_by(desc(Goal.created_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_completed_goals_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> list[Goal]:
        result = await db.execute(
            select(self.model)
            .filter(Goal.owner_id == owner_id, Goal.is_completed == True)
            .order_by(desc(Goal.updated_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def update_progress(
        self, db: AsyncSession, *, goal_id: int, owner_id: int, amount: float
    ) -> Goal:
        goal = await self.get(db=db, id=goal_id)
        if not goal or goal.owner_id != owner_id:
            return None
        
        new_current_amount = goal.current_amount + amount
        
        # Check if goal is completed
        is_completed = new_current_amount >= goal.target_amount
        
        await db.execute(
            updateDb(self.model)
            .where(and_(self.model.id == goal_id, self.model.owner_id == owner_id))
            .values(current_amount=new_current_amount, is_completed=is_completed)
            .execution_options(synchronize_session="fetch")
        )
        await db.commit()
        
        # Return updated goal
        return await self.get(db=db, id=goal_id)

    async def mark_completed(
        self, db: AsyncSession, *, goal_id: int, owner_id: int
    ) -> Goal:
        await db.execute(
            updateDb(self.model)
            .where(and_(self.model.id == goal_id, self.model.owner_id == owner_id))
            .values(is_completed=True)
            .execution_options(synchronize_session="fetch")
        )
        await db.commit()
        
        return await self.get(db=db, id=goal_id)

    async def remove_multi(self, db: AsyncSession, *, ids: list[int]) -> list[Goal]:
        removed_goals = []
        for id in ids:
            goal = await self.remove(db, id=id)
            if goal:
                removed_goals.append(goal)
        return removed_goals


goal = CRUDGoal(Goal)