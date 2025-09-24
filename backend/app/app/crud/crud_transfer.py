from datetime import datetime
from typing import Optional

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
            owner_id=owner_id,
            id=obj_in_data["from_acc"],
            column="total_transfers_out",
            amount=obj_in_data["amount"],
        )

        if update_from is None:
            return None

        update_to = await crud.account.update_by_id_and_field(
            db=db,
            owner_id=owner_id,
            id=obj_in_data["to_acc"],
            column="total_transfers_in",
            amount=obj_in_data["amount"],
        )

        if update_to is None:
            return None

        # Update goal progress if goal_id is provided
        if obj_in_data.get("goal_id"):
            goal = await crud.goal.get(db=db, id=obj_in_data["goal_id"])
            if goal and goal.owner_id == owner_id:
                # For transfers, we consider them as positive contributions towards the goal
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

    async def update_with_owner(
        self, db: AsyncSession, *, db_obj: Transfer, obj_in: TransferUpdate, owner_id: int
    ) -> Transfer:
        if db_obj.owner_id != owner_id:
            return None

        old_amount = db_obj.amount
        old_goal_id = db_obj.goal_id
        old_from_acc = db_obj.from_acc
        old_to_acc = db_obj.to_acc

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
        new_from_acc = db_obj.from_acc
        new_to_acc = db_obj.to_acc

        # Handle account balance changes
        if old_from_acc != new_from_acc or old_amount != new_amount:
            # Revert old from account
            if old_from_acc:
                await crud.account.update_by_id_and_field(
                    db=db,
                    owner_id=owner_id,
                    id=old_from_acc,
                    column="total_transfers_out",
                    amount=-old_amount,
                )

            # Update new from account
            if new_from_acc:
                from_update = await crud.account.update_by_id_and_field(
                    db=db,
                    owner_id=owner_id,
                    id=new_from_acc,
                    column="total_transfers_out",
                    amount=new_amount,
                )
                if from_update is None:
                    return None

        if old_to_acc != new_to_acc or old_amount != new_amount:
            # Revert old to account
            if old_to_acc:
                await crud.account.update_by_id_and_field(
                    db=db,
                    owner_id=owner_id,
                    id=old_to_acc,
                    column="total_transfers_in",
                    amount=-old_amount,
                )

            # Update new to account
            if new_to_acc:
                to_update = await crud.account.update_by_id_and_field(
                    db=db,
                    owner_id=owner_id,
                    id=new_to_acc,
                    column="total_transfers_in",
                    amount=new_amount,
                )
                if to_update is None:
                    return None

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
        self, db: AsyncSession, *, transfer_id: int, owner_id: int
    ) -> Optional[Transfer]:
        result = await db.execute(
            select(self.model)
            .filter(and_(Transfer.owner_id == owner_id, Transfer.id == transfer_id))
        )
        db_obj = result.scalars().first()

        if not db_obj:
            return None

        # Store values before deletion for cleanup
        old_amount = db_obj.amount
        old_goal_id = db_obj.goal_id
        old_from_acc = db_obj.from_acc
        old_to_acc = db_obj.to_acc

        # Revert account balances
        if old_from_acc:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=owner_id,
                id=old_from_acc,
                column="total_transfers_out",
                amount=-old_amount,
            )

        if old_to_acc:
            await crud.account.update_by_id_and_field(
                db=db,
                owner_id=owner_id,
                id=old_to_acc,
                column="total_transfers_in",
                amount=-old_amount,
            )

        # Delete the transfer
        await db.delete(db_obj)
        await db.commit()

        # Recalculate goal progress if it was linked to a goal
        if old_goal_id:
            await crud.goal.recalculate_goal_progress(db=db, goal_id=old_goal_id)

        return db_obj


transfer = CRUDTransfer(Transfer)
