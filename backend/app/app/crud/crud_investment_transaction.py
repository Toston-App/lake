from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import select

from app.crud.base import CRUDBase
from app.models.holding import Holding
from app.models.investment_transaction import InvestmentTransaction, TransactionType
from app.schemas.investment_transaction import (
    InvestmentTransactionCreate,
    InvestmentTransactionUpdate,
)


class CRUDInvestmentTransaction(
    CRUDBase[
        InvestmentTransaction, InvestmentTransactionCreate, InvestmentTransactionUpdate
    ]
):
    @staticmethod
    def _with_asset(query):
        return query.options(
            selectinload(InvestmentTransaction.holding).selectinload(Holding.asset)
        )

    async def get_by_id_and_owner(
        self, db: AsyncSession, *, transaction_id: int, owner_id: int
    ) -> InvestmentTransaction | None:
        result = await db.execute(
            self._with_asset(select(self.model)).filter(
                InvestmentTransaction.id == transaction_id,
                InvestmentTransaction.owner_id == owner_id,
            )
        )
        return result.scalars().first()

    async def get_by_idempotency_key(
        self, db: AsyncSession, *, owner_id: int, idempotency_key: str
    ) -> InvestmentTransaction | None:
        result = await db.execute(
            self._with_asset(select(self.model)).filter(
                InvestmentTransaction.owner_id == owner_id,
                InvestmentTransaction.idempotency_key == idempotency_key,
            )
        )
        return result.scalars().first()

    async def create_with_owner(
        self,
        db: AsyncSession,
        *,
        obj_in: InvestmentTransactionCreate,
        owner_id: int,
        idempotency_key: str,
        request_fingerprint: str,
        commit: bool = True,
    ) -> InvestmentTransaction:
        """Create a new investment transaction."""
        # Use dict() instead of jsonable_encoder to preserve datetime objects
        obj_in_data = obj_in.dict(exclude_unset=False)

        # BUY total_amount is gross acquisition cost; fees are added to holding cost
        # basis. SELL total_amount is net realized proceeds after disposal fees.
        gross_amount = obj_in_data["quantity"] * obj_in_data["price_per_unit"]
        if obj_in_data["transaction_type"] == TransactionType.SELL:
            obj_in_data["total_amount"] = gross_amount - obj_in_data["fees"]
        else:
            obj_in_data["total_amount"] = gross_amount

        # Ensure executed_at is a datetime object
        if isinstance(obj_in_data.get("executed_at"), str):
            obj_in_data["executed_at"] = datetime.fromisoformat(
                obj_in_data["executed_at"].replace("Z", "+00:00")
            )

        db_obj = self.model(
            **obj_in_data,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        db.add(db_obj)
        if commit:
            await db.commit()
        else:
            await db.flush()
        await db.refresh(db_obj)
        return db_obj

    async def get_by_owner(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InvestmentTransaction]:
        """Get all transactions for a user."""
        result = await db.execute(
            self._with_asset(select(self.model))
            .filter(InvestmentTransaction.owner_id == owner_id)
            .order_by(InvestmentTransaction.executed_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_holding(
        self,
        db: AsyncSession,
        *,
        holding_id: int,
        owner_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InvestmentTransaction]:
        """Get all transactions for a specific holding."""
        result = await db.execute(
            self._with_asset(select(self.model))
            .filter(
                InvestmentTransaction.holding_id == holding_id,
                InvestmentTransaction.owner_id == owner_id,
            )
            .order_by(InvestmentTransaction.executed_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_account(
        self,
        db: AsyncSession,
        *,
        account_id: int,
        owner_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InvestmentTransaction]:
        """Get all transactions for a specific account."""
        result = await db.execute(
            self._with_asset(select(self.model))
            .filter(
                InvestmentTransaction.account_id == account_id,
                InvestmentTransaction.owner_id == owner_id,
            )
            .order_by(InvestmentTransaction.executed_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_type(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        transaction_type: TransactionType,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InvestmentTransaction]:
        """Get transactions of a specific type."""
        result = await db.execute(
            self._with_asset(select(self.model))
            .filter(
                InvestmentTransaction.owner_id == owner_id,
                InvestmentTransaction.transaction_type == transaction_type,
            )
            .order_by(InvestmentTransaction.executed_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_date_range(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        start_date: datetime,
        end_date: datetime,
        skip: int = 0,
        limit: int = 100,
    ) -> list[InvestmentTransaction]:
        """Get transactions within a date range."""
        result = await db.execute(
            self._with_asset(select(self.model))
            .filter(
                InvestmentTransaction.owner_id == owner_id,
                InvestmentTransaction.executed_at >= start_date,
                InvestmentTransaction.executed_at <= end_date,
            )
            .order_by(InvestmentTransaction.executed_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_total_fees(self, db: AsyncSession, *, owner_id: int) -> float:
        """Calculate total fees paid by user."""
        from sqlalchemy import func

        result = await db.execute(
            select(func.sum(InvestmentTransaction.fees)).filter(
                InvestmentTransaction.owner_id == owner_id
            )
        )
        total = result.scalar()
        return total or 0.0


investment_transaction = CRUDInvestmentTransaction(InvestmentTransaction)
