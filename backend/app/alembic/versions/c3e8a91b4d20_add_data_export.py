"""Add data_export table for user data downloads.

Revision ID: c3e8a91b4d20
Revises: 2f7c9e4a1b63
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3e8a91b4d20"
down_revision = "2f7c9e4a1b63"
branch_labels = None
depends_on = None

dataexportstatus = sa.Enum(
    "pending",
    "processing",
    "ready",
    "failed",
    "expired",
    name="dataexportstatus",
)


def upgrade() -> None:
    dataexportstatus.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "data_export",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("status", dataexportstatus, nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_step", sa.String(), nullable=True),
        sa.Column("object_key", sa.String(), nullable=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_sha256", sa.String(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_data_export_owner_id", "data_export", ["owner_id"])
    op.create_index("ix_data_export_status", "data_export", ["status"])
    op.create_index(
        "uq_data_export_owner_inflight",
        "data_export",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index("uq_data_export_owner_inflight", table_name="data_export")
    op.drop_index("ix_data_export_status", table_name="data_export")
    op.drop_index("ix_data_export_owner_id", table_name="data_export")
    op.drop_table("data_export")
    dataexportstatus.drop(op.get_bind(), checkfirst=True)
