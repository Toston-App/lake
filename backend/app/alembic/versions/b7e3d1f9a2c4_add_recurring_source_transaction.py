"""add source transaction to recurring transactions

Revision ID: b7e3d1f9a2c4
Revises: a4c9e2f7b1d3
Create Date: 2026-11-02 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "b7e3d1f9a2c4"
down_revision = "a4c9e2f7b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recurring_transaction",
        sa.Column("source_transaction_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recurring_transaction", "source_transaction_id")
