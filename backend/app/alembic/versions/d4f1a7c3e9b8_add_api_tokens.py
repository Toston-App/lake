"""add api tokens

Revision ID: d4f1a7c3e9b8
Revises: c5d8a3e1f7b2
Create Date: 2026-11-11 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "d4f1a7c3e9b8"
down_revision = "c5d8a3e1f7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=12), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_token_id", "api_token", ["id"])
    op.create_index("ix_api_token_owner_id", "api_token", ["owner_id"])
    op.create_index("ix_api_token_token_hash", "api_token", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_token_token_hash", table_name="api_token")
    op.drop_index("ix_api_token_owner_id", table_name="api_token")
    op.drop_index("ix_api_token_id", table_name="api_token")
    op.drop_table("api_token")
