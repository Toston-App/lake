"""Add portfolio_snapshot and portfolio_snapshot_holding tables.

Revision ID: a7d41f0c9e52
Revises: c3e8a91b4d20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "a7d41f0c9e52"
down_revision = "c3e8a91b4d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "portfolio_snapshot" in inspector.get_table_names():
        return

    op.create_table(
        "portfolio_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_value_usd", sa.Numeric(38, 8), nullable=False),
        sa.Column("total_value_mxn", sa.Numeric(38, 8), nullable=False),
        sa.Column("total_invested_usd", sa.Numeric(38, 8), nullable=False),
        sa.Column("total_invested_mxn", sa.Numeric(38, 8), nullable=False),
        sa.Column("usd_mxn_rate", sa.Numeric(20, 12), nullable=False),
        sa.Column("holdings_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "snapshot_date", name="uq_portfolio_snapshot_owner_date"
        ),
        sa.CheckConstraint(
            "total_value_usd >= 0 AND total_value_mxn >= 0",
            name="ck_portfolio_snapshot_value_nonnegative",
        ),
        sa.CheckConstraint(
            "total_invested_usd >= 0 AND total_invested_mxn >= 0",
            name="ck_portfolio_snapshot_invested_nonnegative",
        ),
        sa.CheckConstraint(
            "usd_mxn_rate > 0", name="ck_portfolio_snapshot_rate_positive"
        ),
    )
    op.create_table(
        "portfolio_snapshot_holding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("holding_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("value_usd", sa.Numeric(38, 8), nullable=False),
        sa.Column("value_mxn", sa.Numeric(38, 8), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["portfolio_snapshot.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "holding_id", name="uq_portfolio_snapshot_holding"
        ),
    )
    op.create_index("ix_portfolio_snapshot_id", "portfolio_snapshot", ["id"])
    op.create_index(
        "ix_portfolio_snapshot_owner_id", "portfolio_snapshot", ["owner_id"]
    )
    op.create_index(
        "ix_portfolio_snapshot_holding_id", "portfolio_snapshot_holding", ["id"]
    )
    op.create_index(
        "ix_portfolio_snapshot_holding_snapshot_id",
        "portfolio_snapshot_holding",
        ["snapshot_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "portfolio_snapshot" not in inspector.get_table_names():
        return
    op.drop_index(
        "ix_portfolio_snapshot_holding_snapshot_id",
        table_name="portfolio_snapshot_holding",
    )
    op.drop_index(
        "ix_portfolio_snapshot_holding_id", table_name="portfolio_snapshot_holding"
    )
    op.drop_index("ix_portfolio_snapshot_owner_id", table_name="portfolio_snapshot")
    op.drop_index("ix_portfolio_snapshot_id", table_name="portfolio_snapshot")
    op.drop_table("portfolio_snapshot_holding")
    op.drop_table("portfolio_snapshot")
