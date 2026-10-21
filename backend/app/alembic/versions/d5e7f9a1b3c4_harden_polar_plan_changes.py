"""harden Polar plan changes

Revision ID: d5e7f9a1b3c4
Revises: c3d9e7f1a2b4
Create Date: 2026-07-21 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "d5e7f9a1b3c4"
down_revision = "c3d9e7f1a2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_subscription", sa.Column("pending_tier", sa.String(), nullable=True)
    )
    op.add_column(
        "user_subscription",
        sa.Column("pending_effective_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_chat_obs_turns_user_started_at",
        "chat_obs_turns",
        ["user_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_obs_turns_user_started_at", table_name="chat_obs_turns")
    op.drop_column("user_subscription", "pending_effective_at")
    op.drop_column("user_subscription", "pending_tier")
