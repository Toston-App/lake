from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base


class PortfolioSnapshot(Base):
    """
    One valuation of a user's whole portfolio per calendar day.

    Upserted by the portfolio snapshot worker; the last run of the day wins.
    total_invested_usd / total_invested_mxn are the RAW cost basis of holdings
    whose cost currency is USD / MXN (same meaning as /portfolio/summary).
    """

    __tablename__ = "portfolio_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "snapshot_date", name="uq_portfolio_snapshot_owner_date"
        ),
        CheckConstraint(
            "total_value_usd >= 0 AND total_value_mxn >= 0",
            name="ck_portfolio_snapshot_value_nonnegative",
        ),
        CheckConstraint(
            "total_invested_usd >= 0 AND total_invested_mxn >= 0",
            name="ck_portfolio_snapshot_invested_nonnegative",
        ),
        CheckConstraint("usd_mxn_rate > 0", name="ck_portfolio_snapshot_rate_positive"),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_date: date = Column(Date, nullable=False)
    # When the values were last written; used to place splits between two
    # snapshots when computing performance.
    captured_at: datetime = Column(DateTime(timezone=True), nullable=False)

    total_value_usd: Decimal = Column(Numeric(38, 8), nullable=False)
    total_value_mxn: Decimal = Column(Numeric(38, 8), nullable=False)
    total_invested_usd: Decimal = Column(Numeric(38, 8), nullable=False)
    total_invested_mxn: Decimal = Column(Numeric(38, 8), nullable=False)
    usd_mxn_rate: Decimal = Column(Numeric(20, 12), nullable=False)
    holdings_count: int = Column(Integer, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    positions: list["PortfolioSnapshotHolding"] = relationship(
        "PortfolioSnapshotHolding",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PortfolioSnapshotHolding(Base):
    """One holding's position inside a PortfolioSnapshot."""

    __tablename__ = "portfolio_snapshot_holding"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "holding_id", name="uq_portfolio_snapshot_holding"
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    snapshot_id: int = Column(
        Integer,
        ForeignKey("portfolio_snapshot.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # No FK: holdings can be deleted while their history must remain.
    holding_id: int = Column(Integer, nullable=False)
    asset_id: int = Column(Integer, nullable=False)
    quantity: Decimal = Column(Numeric(28, 12), nullable=False)
    value_usd: Decimal = Column(Numeric(38, 8), nullable=False)
    value_mxn: Decimal = Column(Numeric(38, 8), nullable=False)
