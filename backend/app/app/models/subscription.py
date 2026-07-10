"""User subscription state, synced from Polar.sh webhooks.

A single row per user (unique `user_id`). The Polar webhook handler is the
only writer; the entitlements service reads it (with a Redis cache) to gate
chat usage and tool availability.
"""

from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401


class UserSubscription(Base):
    __tablename__ = "user_subscription"

    id: int = Column(Integer, primary_key=True, index=True)
    user_id: int = Column(
        Integer, ForeignKey("user.id"), unique=True, nullable=False, index=True
    )
    # Polar customer id (stable across subscriptions). Denormalized on User
    # too for checkout creation; kept here for self-contained webhook writes.
    polar_customer_id: str | None = Column(String, index=True, nullable=True)
    polar_subscription_id: str | None = Column(String, index=True, nullable=True)
    # Tier enum value ("free" | "tier1" | "tier2" | "tier3").
    tier: str = Column(String, nullable=False, default="free", index=True)
    # free | active | canceled | past_due | incomplete | ...
    status: str = Column(String, nullable=False, default="free", index=True)
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    # Timestamp of the newest Polar event applied to this row (ordering guard).
    last_event_at = Column(DateTime(timezone=True), nullable=True)
    # Raw Polar payload (last webhook event) for debugging/auditing.
    polar_metadata = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user: "User" = relationship(
        "User", back_populates="subscription", uselist=False
    )
