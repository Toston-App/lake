"""Idempotency ledger for Polar Standard Webhooks deliveries."""

from sqlalchemy import Column, DateTime, String
from sqlalchemy.sql import func

from app.db.base_class import Base


class PolarWebhookEvent(Base):
    __tablename__ = "polar_webhook_event"

    # Standard Webhooks `webhook-id` header (unique per delivery).
    webhook_id: str = Column(String, primary_key=True)
    event_type: str | None = Column(String, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
