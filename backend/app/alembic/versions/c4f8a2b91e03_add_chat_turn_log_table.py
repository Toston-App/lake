"""add chat turn log table (recovered orphan)

This is a recovered placeholder for a migration that was previously applied
to production but whose source file was deleted (only the compiled .pyc
remained in __pycache__). The original created a flat ``chatturnlog`` table
as an early observability attempt.

It is recreated here as a no-op so Alembic can recognise the revision
``c4f8a2b91e03`` that is already stamped in existing databases, and so the
replacement migration ``f7a2c1e9b4d0`` (which migrates any ``chatturnlog``
data into the new ``chat_obs_*`` tables and drops the orphan) can chain on
top of it. Fresh databases never create ``chatturnlog``; the data-migration
in the follow-up is guarded for its absence.

Revision ID: c4f8a2b91e03
Revises: 1b088076d25c
Create Date: 2026-06-12 16:38:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "c4f8a2b91e03"
down_revision = "1b088076d25c"
branch_labels = None
depends_on = None


def upgrade():
    # No-op: the original DDL was already applied to existing databases, and
    # fresh databases should not create the deprecated chatturnlog table.
    pass


def downgrade():
    # Drop the orphan table if it happens to exist (best-effort cleanup).
    op.execute("DROP TABLE IF EXISTS chatturnlog")
