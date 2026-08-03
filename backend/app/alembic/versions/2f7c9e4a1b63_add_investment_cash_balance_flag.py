"""Add optional cash balance handling to investment transactions.

Revision ID: 2f7c9e4a1b63
Revises: 8d3c4f1a2b90
"""

import sqlalchemy as sa
from alembic import op

revision = "2f7c9e4a1b63"
down_revision = "8d3c4f1a2b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "investmenttransaction",
        sa.Column(
            "affects_cash_balance",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("investmenttransaction", "affects_cash_balance")
