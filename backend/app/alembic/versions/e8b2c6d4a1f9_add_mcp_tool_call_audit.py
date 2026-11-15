"""add mcp tool call audit log

Revision ID: e8b2c6d4a1f9
Revises: d4f1a7c3e9b8
Create Date: 2026-11-11 00:00:00.000001
"""

import sqlalchemy as sa
from alembic import op

revision = "e8b2c6d4a1f9"
down_revision = "d4f1a7c3e9b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_tool_call",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client", sa.String(length=220), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("action_id", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_mcp_tool_call_user_created_at", "mcp_tool_call", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_tool_call_user_created_at", table_name="mcp_tool_call")
    op.drop_table("mcp_tool_call")
