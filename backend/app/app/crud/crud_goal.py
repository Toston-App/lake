from datetime import datetime
from typing import List, Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.goal import Goal, GoalStatus
from app.schemas.goal import GoalCreate, GoalStats, GoalUpdate


class CRUDGoal(CRUDBase[Goal, GoalCreate, GoalUpdate]):
    async def create_with_owner(
        self, db: AsyncSession, *, obj_in: GoalCreate, owner_id: int
    ) -> Goal:
        obj_in_data = jsonable_encoder(obj_in)
        db_obj = self.model(**obj_in_data, owner_id=owner_id)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_multi_by_owner(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        skip: int = 0,
        limit: int = 100,
        status: Optional[GoalStatus] = None
    ) -> List[Goal]:
        query = select(self.model).where(self.model.owner_id == owner_id)
        if status:
            query = query.where(self.model.status == status)
        query = query.offset(skip).limit(limit).order_by(self.model.created_at.desc())
        result = await db.execute(query)
        return result.scalars().all()

    async def get_by_id_and_owner(
        self, db: AsyncSession, *, id: int, owner_id: int
    ) -> Optional[Goal]:
        result = await db.execute(
            select(self.model).where(
                and_(self.model.id == id, self.model.owner_id == owner_id)
            )
        )
        return result.scalars().first()

    async def update_by_id_and_owner(
        self,
        db: AsyncSession,
        *,
        id: int,
        owner_id: int,
        obj_in: GoalUpdate
    ) -> Optional[Goal]:
        db_obj = await self.get_by_id_and_owner(db=db, id=id, owner_id=owner_id)
        if db_obj:
            obj_data = jsonable_encoder(db_obj)
            update_data = obj_in.dict(exclude_unset=True)
            
            # Auto-complete goal if current_amount >= target_amount
            if "current_amount" in update_data:
                if update_data["current_amount"] >= db_obj.target_amount:
                    update_data["status"] = GoalStatus.COMPLETED
                    update_data["completed_at"] = datetime.utcnow()
            
            for field in obj_data:
                if field in update_data:
                    setattr(db_obj, field, update_data[field])
            
            db.add(db_obj)
            await db.commit()
            await db.refresh(db_obj)
        return db_obj

    async def delete_by_id_and_owner(
        self, db: AsyncSession, *, id: int, owner_id: int
    ) -> Optional[Goal]:
        obj = await self.get_by_id_and_owner(db=db, id=id, owner_id=owner_id)
        if obj:
            await db.delete(obj)
            await db.commit()
        return obj

    async def add_to_goal(
        self, db: AsyncSession, *, goal_id: int, owner_id: int, amount: float
    ) -> Optional[Goal]:
        goal = await self.get_by_id_and_owner(db=db, id=goal_id, owner_id=owner_id)
        if goal:
            new_amount = goal.current_amount + amount
            update_data = GoalUpdate(current_amount=new_amount)
            return await self.update_by_id_and_owner(
                db=db, id=goal_id, owner_id=owner_id, obj_in=update_data
            )
        return None

    async def get_stats_by_owner(
        self, db: AsyncSession, *, owner_id: int
    ) -> GoalStats:
        # Count total goals
        total_result = await db.execute(
            select(func.count(self.model.id)).where(self.model.owner_id == owner_id)
        )
        total_goals = total_result.scalar() or 0

        # Count active goals
        active_result = await db.execute(
            select(func.count(self.model.id)).where(
                and_(
                    self.model.owner_id == owner_id,
                    self.model.status == GoalStatus.ACTIVE
                )
            )
        )
        active_goals = active_result.scalar() or 0

        # Count completed goals
        completed_result = await db.execute(
            select(func.count(self.model.id)).where(
                and_(
                    self.model.owner_id == owner_id,
                    self.model.status == GoalStatus.COMPLETED
                )
            )
        )
        completed_goals = completed_result.scalar() or 0

        # Sum target amounts
        target_result = await db.execute(
            select(func.sum(self.model.target_amount)).where(
                self.model.owner_id == owner_id
            )
        )
        total_target_amount = target_result.scalar() or 0.0

        # Sum current amounts
        current_result = await db.execute(
            select(func.sum(self.model.current_amount)).where(
                self.model.owner_id == owner_id
            )
        )
        total_current_amount = current_result.scalar() or 0.0

        # Calculate average progress
        average_progress = 0.0
        if total_goals > 0 and total_target_amount > 0:
            average_progress = (total_current_amount / total_target_amount) * 100.0

        return GoalStats(
            total_goals=total_goals,
            active_goals=active_goals,
            completed_goals=completed_goals,
            total_target_amount=total_target_amount,
            total_current_amount=total_current_amount,
            average_progress=round(average_progress, 2)
        )


goal = CRUDGoal(Goal)