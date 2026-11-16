"""add user mcp_read_only

Revision ID: f3a9d2b7c5e1
Revises: e8b2c6d4a1f9
Create Date: 2026-11-11 00:00:00.000002
"""

import sqlalchemy as sa
from alembic import op

revision = "f3a9d2b7c5e1"
down_revision = "e8b2c6d4a1f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "mcp_read_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "mcp_read_only")
