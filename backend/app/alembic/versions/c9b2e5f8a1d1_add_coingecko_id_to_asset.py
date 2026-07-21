"""add_coingecko_id_to_asset

Revision ID: c9b2e5f8a1d1
Revises: 14c4f623d063
Create Date: 2026-04-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c9b2e5f8a1d1"
down_revision = "14c4f623d063"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("asset", sa.Column("coingecko_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_asset_coingecko_id"), "asset", ["coingecko_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_asset_coingecko_id"), table_name="asset")
    op.drop_column("asset", "coingecko_id")
