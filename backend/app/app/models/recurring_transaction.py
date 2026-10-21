from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.recurrence import (
    BusinessDayAdjustment,
    Frequency,
    MonthlyMode,
    RecurringType,
)
from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401


def _enum(enum_cls, name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [x.value for x in e])


class RecurringTransaction(Base):
    """Template that generates expenses, incomes or transfers on a schedule."""

    __tablename__ = "recurring_transaction"
    __table_args__ = (
        Index(
            "ix_recurring_transaction_due",
            "next_run_date",
            postgresql_where=text("is_active AND next_run_date IS NOT NULL"),
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner: "User" = relationship("User", back_populates="recurring_transactions")
    type: RecurringType = Column(
        _enum(RecurringType, "recurringtransactiontype"), nullable=False
    )

    # Fields copied to every generated transaction.
    amount: float = Column(Float, nullable=False)
    description: str | None = Column(String, nullable=True)
    account_id: int | None = Column(
        Integer, ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    to_account_id: int | None = Column(
        Integer, ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    category_id: int | None = Column(
        Integer, ForeignKey("category.id", ondelete="SET NULL"), nullable=True
    )
    subcategory_id: int | None = Column(
        Integer, ForeignKey("subcategory.id", ondelete="SET NULL"), nullable=True
    )
    place_id: int | None = Column(
        Integer, ForeignKey("place.id", ondelete="SET NULL"), nullable=True
    )

    # Schedule rule, see app.core.recurrence.
    frequency: Frequency = Column(
        _enum(Frequency, "recurrencefrequency"), nullable=False
    )
    interval: int = Column(Integer, nullable=False, default=1, server_default="1")
    weekdays: list[int] = Column(
        ARRAY(Integer), nullable=False, default=list, server_default="{}"
    )
    monthly_mode: MonthlyMode = Column(
        _enum(MonthlyMode, "recurrencemonthlymode"),
        nullable=False,
        default=MonthlyMode.day_of_month,
        server_default=MonthlyMode.day_of_month.value,
    )
    month_days: list[int] = Column(
        ARRAY(Integer), nullable=False, default=list, server_default="{}"
    )
    week_of_month: int | None = Column(Integer, nullable=True)
    business_day_adjustment: BusinessDayAdjustment = Column(
        _enum(BusinessDayAdjustment, "businessdayadjustment"),
        nullable=False,
        default=BusinessDayAdjustment.none,
        server_default=BusinessDayAdjustment.none.value,
    )
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    max_occurrences: int | None = Column(Integer, nullable=True)
    timezone: str = Column(String, nullable=False, default="UTC", server_default="UTC")

    # Progress. next_run_date is NULL once the series has ended.
    is_active: bool = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    occurrences_count: int = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_nominal_date = Column(Date, nullable=True)
    next_run_date = Column(Date, nullable=True)
    last_run_date = Column(Date, nullable=True)
    last_error: str | None = Column(String, nullable=True)
    # Short lease so only one worker (or request) generates a template at a time.
    locked_until = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
