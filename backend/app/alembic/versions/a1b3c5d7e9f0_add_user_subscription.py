"""add user_subscription table and user.polar_customer_id

Revision ID: a1b3c5d7e9f0
Revises: f7a2c1e9b4d0
Create Date: 2026-06-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a1b3c5d7e9f0"
down_revision = "f7a2c1e9b4d0"
branch_labels = None
depends_on = None


def upgrade():
    # 1. New table tracking each user's Polar subscription state.
    op.create_table(
        "user_subscription",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("polar_customer_id", sa.String(), nullable=True),
        sa.Column("polar_subscription_id", sa.String(), nullable=True),
        sa.Column("tier", sa.String(), nullable=False, server_default="free"),
        sa.Column("status", sa.String(), nullable=False, server_default="free"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("polar_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_subscription_user_id"),
    )
    op.create_index("ix_user_subscription_user_id", "user_subscription", ["user_id"])
    op.create_index(
        "ix_user_subscription_polar_customer_id",
        "user_subscription",
        ["polar_customer_id"],
    )
    op.create_index("ix_user_subscription_tier", "user_subscription", ["tier"])
    op.create_index("ix_user_subscription_status", "user_subscription", ["status"])

    # 2. Denormalized Polar customer id on the user row.
    op.add_column(
        "user",
        sa.Column("polar_customer_id", sa.String(), nullable=True),
    )
    op.create_index("ix_user_polar_customer_id", "user", ["polar_customer_id"])

    # 3. Backfill a free-tier subscription row for every existing user.
    op.execute(
        """
        INSERT INTO user_subscription (user_id, tier, status)
        SELECT id, 'free', 'free' FROM "user"
        ON CONFLICT (user_id) DO NOTHING
        """
    )


def downgrade():
    op.drop_index("ix_user_polar_customer_id", table_name="user")
    op.drop_column("user", "polar_customer_id")
    op.drop_index("ix_user_subscription_status", table_name="user_subscription")
    op.drop_index("ix_user_subscription_tier", table_name="user_subscription")
    op.drop_index(
        "ix_user_subscription_polar_customer_id", table_name="user_subscription"
    )
    op.drop_index("ix_user_subscription_user_id", table_name="user_subscription")
    op.drop_table("user_subscription")
