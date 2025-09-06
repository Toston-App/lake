from datetime import datetime, date
from typing import List, Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, or_, update as updateDb
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select
from sqlalchemy.sql import func

from app import crud
from app.crud.base import CRUDBase
from app.models.goal import Goal, GoalStatus
from app.schemas.goal import GoalCreate, GoalUpdate


class CRUDGoal(CRUDBase[Goal, GoalCreate, GoalUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: GoalCreate, owner_id: int
    ) -> Goal:
        obj_in_data = jsonable_encoder(obj_in)
        
        # Validate linked account ownership if provided
        if obj_in_data.get("linked_account_id"):
            account = await crud.account.get(db=db, id=obj_in_data["linked_account_id"])
            if not account or account.owner_id != owner_id:
                obj_in_data["linked_account_id"] = None
        
        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_multi_by_owner(
        self, db: AsyncSession, *, owner_id: int, skip: int = 0, limit: int = 100
    ) -> List[Goal]:
        result = await db.execute(
            select(self.model)
            .filter(Goal.owner_id == owner_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_owner_and_id(
        self, db: AsyncSession, *, owner_id: int, goal_id: int
    ) -> Optional[Goal]:
        result = await db.execute(
            select(self.model)
            .filter(and_(Goal.owner_id == owner_id, Goal.id == goal_id))
        )
        return result.scalars().first()

    async def update_with_owner(
        self, db: AsyncSession, *, db_obj: Goal, obj_in: GoalUpdate, owner_id: int
    ) -> Goal:
        if db_obj.owner_id != owner_id:
            return None
        
        obj_in_data = jsonable_encoder(obj_in)
        
        # Validate linked account ownership if being updated
        if obj_in_data.get("linked_account_id"):
            account = await crud.account.get(db=db, id=obj_in_data["linked_account_id"])
            if not account or account.owner_id != owner_id:
                obj_in_data["linked_account_id"] = None
        
        # Update the object
        for field, value in obj_in_data.items():
            if value is not None:
                setattr(db_obj, field, value)
        
        # Set updated_at timestamp
        db_obj.updated_at = datetime.utcnow()
        
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def remove_with_owner(
        self, db: AsyncSession, *, goal_id: int, owner_id: int
    ) -> Optional[Goal]:
        db_obj = await self.get_by_owner_and_id(
            db=db, owner_id=owner_id, goal_id=goal_id
        )
        if db_obj:
            await db.delete(db_obj)
            await db.commit()
            return db_obj
        return None

    async def update_goal_amount(
        self, db: AsyncSession, *, goal_id: int, amount: float, is_positive: bool = True
    ) -> Optional[Goal]:
        goal = await self.get(db=db, id=goal_id)
        if not goal:
            return None
        
        # Update current amount (add for transfers, subtract for expenses)
        if is_positive:
            goal.current_amount += amount
        else:
            goal.current_amount = max(0, goal.current_amount - amount)
        
        # Check if goal is completed
        if goal.current_amount >= goal.target_amount and goal.status == GoalStatus.ACTIVE:
            goal.status = GoalStatus.COMPLETED
            goal.completed_at = datetime.utcnow()
        
        # Check if goal is overdue
        if goal.deadline and date.today() > goal.deadline and goal.status == GoalStatus.ACTIVE:
            goal.status = GoalStatus.OVERDUE
        
        goal.updated_at = datetime.utcnow()
        
        db.add(goal)
        await db.commit()
        await db.refresh(goal)
        return goal

    async def get_goals_by_status(
        self, db: AsyncSession, *, owner_id: int, status: GoalStatus
    ) -> List[Goal]:
        result = await db.execute(
            select(self.model)
            .filter(and_(Goal.owner_id == owner_id, Goal.status == status))
        )
        return result.scalars().all()

    async def get_overdue_goals(self, db: AsyncSession, *, owner_id: int) -> List[Goal]:
        today = date.today()
        result = await db.execute(
            select(self.model)
            .filter(
                and_(
                    Goal.owner_id == owner_id,
                    Goal.deadline < today,
                    Goal.status == GoalStatus.ACTIVE
                )
            )
        )
        goals = result.scalars().all()
        
        # Update status for overdue goals
        for goal in goals:
            goal.status = GoalStatus.OVERDUE
            goal.updated_at = datetime.utcnow()
            db.add(goal)
        
        if goals:
            await db.commit()
        
        return goals

    async def get_goal_progress_stats(
        self, db: AsyncSession, *, owner_id: int
    ) -> dict:
        result = await db.execute(
            select(
                Goal.status,
                func.count(Goal.id).label('count'),
                func.sum(Goal.current_amount).label('total_current'),
                func.sum(Goal.target_amount).label('total_target')
            )
            .filter(Goal.owner_id == owner_id)
            .group_by(Goal.status)
        )
        
        stats = {}
        for row in result:
            stats[row.status] = {
                'count': row.count,
                'total_current_amount': row.total_current or 0,
                'total_target_amount': row.total_target or 0
            }
        
        return stats


goal = CRUDGoal(Goal)