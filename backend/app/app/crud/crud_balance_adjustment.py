from datetime import date as date_type, datetime

from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.balance_adjustment import BalanceAdjustment
from app.schemas.balance_adjustment import (
    BalanceAdjustmentCreate,
    BalanceAdjustmentUpdate,
)


class CRUDBalanceAdjustment(
    CRUDBase[BalanceAdjustment, BalanceAdjustmentCreate, BalanceAdjustmentUpdate]
):
    async def create_with_user(
        self,
        db: AsyncSession,
        *,
        obj_in: BalanceAdjustmentCreate,
        user_id: int,
        old_balance: float,
    ) -> BalanceAdjustment:
        """
        Create a new balance adjustment record.
        
        Args:
            db: Database session
            obj_in: Balance adjustment data (includes account_id, new_balance, description, adjustment_date)
            user_id: ID of the user making the adjustment
            old_balance: The current balance before adjustment
            
        Returns:
            Created BalanceAdjustment object
        """
        obj_in_data = jsonable_encoder(obj_in)
        
        # Calculate adjustment amount
        new_balance = obj_in_data["new_balance"]
        adjustment_amount = new_balance - old_balance
        
        # Handle adjustment_date conversion
        adjustment_date = obj_in_data.get("adjustment_date")
        if adjustment_date is None:
            adjustment_date = date_type.today()
        elif isinstance(adjustment_date, str):
            # Convert string to date object
            try:
                adjustment_date = datetime.strptime(adjustment_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                adjustment_date = date_type.today()
        
        obj_in_data["adjustment_date"] = adjustment_date
        
        db_obj = self.model(
            **obj_in_data,
            user_id=user_id,
            old_balance=old_balance,
            adjustment_amount=adjustment_amount,
        )
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def get_by_account(
        self,
        db: AsyncSession,
        *,
        account_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[BalanceAdjustment]:
        """
        Get all balance adjustments for a specific account, ordered by date (newest first).
        
        Args:
            db: Database session
            account_id: ID of the account
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            List of BalanceAdjustment objects
        """
        result = await db.execute(
            select(self.model)
            .filter(BalanceAdjustment.account_id == account_id)
            .order_by(desc(BalanceAdjustment.adjustment_date), desc(BalanceAdjustment.created_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_user(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[BalanceAdjustment]:
        """
        Get all balance adjustments made by a specific user, ordered by date (newest first).
        
        Args:
            db: Database session
            user_id: ID of the user
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            List of BalanceAdjustment objects
        """
        result = await db.execute(
            select(self.model)
            .filter(BalanceAdjustment.user_id == user_id)
            .order_by(desc(BalanceAdjustment.adjustment_date), desc(BalanceAdjustment.created_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()


balance_adjustment = CRUDBalanceAdjustment(BalanceAdjustment)
