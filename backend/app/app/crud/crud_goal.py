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

        deadline = obj_in_data["deadline"]
        if deadline:
            try:
                obj_in_data["deadline"] = datetime.strptime(deadline, "%Y-%m-%d").date()
            except:
                obj_in_data["deadline"] = None

        start_date = obj_in_data["start_date"]
        if start_date:
            try:
                obj_in_data["start_date"] = datetime.strptime(start_date, "%Y-%m-%d").date()
            except:
                obj_in_data["start_date"] = None

        # Validate linked account ownership if provided (treat 0 / falsy as None)
        if "linked_account_id" in obj_in_data:
            raw_id = obj_in_data.get("linked_account_id")
            if not raw_id:  # catches None, 0, False
                obj_in_data["linked_account_id"] = None
            else:
                account = await crud.account.get(db=db, id=raw_id)
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

        # Check and update status based on changes
        self._update_goal_status(db_obj)

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

        # Update current amount (add for incomes and transfers)
        # Only positive updates are used now, but keeping parameter for flexibility
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

    async def recalculate_goal_progress(
        self, db: AsyncSession, *, goal_id: int
    ) -> Optional[Goal]:
        """
        Recalculate goal progress by summing all linked transactions.
        This should be called when transactions are modified or deleted.
        """
        from app.models.income import Income
        from app.models.transfer import Transfer

        goal = await self.get(db=db, id=goal_id)
        if not goal:
            return None

        # Calculate total from incomes linked to this goal
        income_result = await db.execute(
            select(func.coalesce(func.sum(Income.amount), 0))
            .filter(Income.goal_id == goal_id)
        )
        total_income = income_result.scalar() or 0

        # Calculate total from transfers linked to this goal
        transfer_result = await db.execute(
            select(func.coalesce(func.sum(Transfer.amount), 0))
            .filter(Transfer.goal_id == goal_id)
        )
        total_transfer = transfer_result.scalar() or 0

        # Update goal current amount
        goal.current_amount = total_income + total_transfer

        # Update status based on new amount
        self._update_goal_status(goal)

        # Set updated_at timestamp
        goal.updated_at = datetime.utcnow()

        db.add(goal)
        await db.commit()
        await db.refresh(goal)
        return goal

    def _update_goal_status(self, goal: Goal) -> None:
        """
        Update goal status based on current state.
        This method checks various conditions and updates the status accordingly.
        """
        current_status = goal.status
        today = date.today()

        # Skip status update if goal is already closed
        if current_status == GoalStatus.CLOSED:
            return

        # Check if goal should be marked as completed
        if (goal.current_amount >= goal.target_amount and
            current_status in [GoalStatus.ACTIVE, GoalStatus.OVERDUE]):
            goal.status = GoalStatus.COMPLETED
            goal.completed_at = datetime.utcnow()
            return

        # Check if goal should be marked as overdue
        if (goal.deadline and
            today > goal.deadline and
            current_status == GoalStatus.ACTIVE and
            goal.current_amount < goal.target_amount):
            goal.status = GoalStatus.OVERDUE
            return

        # Check if goal should be reactivated (was overdue but deadline extended)
        if (current_status == GoalStatus.OVERDUE and
            goal.deadline and
            today <= goal.deadline and
            goal.current_amount < goal.target_amount):
            goal.status = GoalStatus.ACTIVE
            return

        # Check if completed goal should be reactivated (target amount increased)
        if (current_status == GoalStatus.COMPLETED and
            goal.current_amount < goal.target_amount):
            goal.status = GoalStatus.ACTIVE
            goal.completed_at = None

            # Check if it should actually be overdue
            if goal.deadline and today > goal.deadline:
                goal.status = GoalStatus.OVERDUE
            return


goal = CRUDGoal(Goal)