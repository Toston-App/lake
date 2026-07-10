"""harden polar subscription binding and webhook idempotency

Revision ID: b2c4d6e8f0a1
Revises: a1b3c5d7e9f0
Create Date: 2026-07-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b2c4d6e8f0a1"
down_revision = "a1b3c5d7e9f0"
branch_labels = None
depends_on = None


def upgrade():
    # Unique polar_customer_id (when set) so one Polar customer maps to one user.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_polar_customer_id
        ON "user" (polar_customer_id)
        WHERE polar_customer_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_subscription_polar_customer_id
        ON user_subscription (polar_customer_id)
        WHERE polar_customer_id IS NOT NULL
        """
    )

    # Event ordering: skip stale webhook payloads that arrive out of order.
    op.add_column(
        "user_subscription",
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Idempotency for Standard Webhooks deliveries (webhook-id header).
    op.create_table(
        "polar_webhook_event",
        sa.Column("webhook_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("webhook_id"),
    )


def downgrade():
    op.drop_table("polar_webhook_event")
    op.drop_column("user_subscription", "last_event_at")
    op.execute("DROP INDEX IF EXISTS uq_user_subscription_polar_customer_id")
    op.execute("DROP INDEX IF EXISTS uq_user_polar_customer_id")
