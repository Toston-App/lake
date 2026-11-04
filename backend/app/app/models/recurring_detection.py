"""Tables used by recurring-series detection (see app.services.recurring_detection)."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db.base_class import Base


class RecurringMerchantAlias(Base):
    """The merchant a normalized description resolves to, cached per user."""

    __tablename__ = "recurring_merchant_alias"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "normalized_description",
            name="uq_recurring_merchant_alias_owner_description",
        ),
    )

    id: int = Column(Integer, primary_key=True)
    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    normalized_description: str = Column(String, nullable=False)
    merchant_key: str = Column(String, nullable=False)
    merchant_name: str = Column(String, nullable=False)
    # "ai" or "rule".
    source: str = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RecurringSuggestionDismissal(Base):
    """A suggestion the user dismissed; it never comes back."""

    __tablename__ = "recurring_suggestion_dismissal"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "fingerprint", name="uq_recurring_dismissal_owner_fingerprint"
        ),
    )

    id: int = Column(Integer, primary_key=True)
    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: str = Column(String(32), nullable=False)
    label: str | None = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RecurringDetectionUsage(Base):
    """Scans a user has made, and the result of the last one.

    `scan_count` counts scans that used up the free scan (free users get one).
    """

    __tablename__ = "recurring_detection_usage"

    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    # "running" while a scan holds the claim, otherwise "done".
    status: str = Column(String, nullable=False, default="done", server_default="done")
    scan_count: int = Column(Integer, nullable=False, default=0, server_default="0")
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    last_scan_at = Column(DateTime(timezone=True), nullable=True)
    last_result: dict | None = Column(JSONB, nullable=True)
