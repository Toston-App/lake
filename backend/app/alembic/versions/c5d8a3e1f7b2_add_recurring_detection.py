"""add recurring detection tables

Revision ID: c5d8a3e1f7b2
Revises: b7e3d1f9a2c4
Create Date: 2026-11-03 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c5d8a3e1f7b2"
down_revision = "b7e3d1f9a2c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recurring_merchant_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("normalized_description", sa.String(), nullable=False),
        sa.Column("merchant_key", sa.String(), nullable=False),
        sa.Column("merchant_name", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "owner_id",
            "normalized_description",
            name="uq_recurring_merchant_alias_owner_description",
        ),
    )
    op.create_table(
        "recurring_suggestion_dismissal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "owner_id", "fingerprint", name="uq_recurring_dismissal_owner_fingerprint"
        ),
    )
    op.create_table(
        "recurring_detection_usage",
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="done"),
        sa.Column("scan_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "recurring_transaction",
        sa.Column("detection_fingerprint", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_recurring_transaction_detection_fingerprint",
        "recurring_transaction",
        ["owner_id", "detection_fingerprint"],
        unique=True,
        postgresql_where=sa.text("detection_fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recurring_transaction_detection_fingerprint",
        table_name="recurring_transaction",
    )
    op.drop_column("recurring_transaction", "detection_fingerprint")
    op.drop_table("recurring_detection_usage")
    op.drop_table("recurring_suggestion_dismissal")
    op.drop_table("recurring_merchant_alias")
