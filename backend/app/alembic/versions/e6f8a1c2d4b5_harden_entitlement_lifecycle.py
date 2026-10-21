"""harden entitlement lifecycle

Revision ID: e6f8a1c2d4b5
Revises: d5e7f9a1b3c4
Create Date: 2026-08-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e6f8a1c2d4b5"
down_revision = "d5e7f9a1b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_subscription",
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "user_subscription",
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_subscription",
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_subscription",
        sa.Column(
            "last_event_priority", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("user_subscription", "last_event_priority")
    op.drop_column("user_subscription", "ended_at")
    op.drop_column("user_subscription", "ends_at")
    op.drop_column("user_subscription", "cancel_at_period_end")
