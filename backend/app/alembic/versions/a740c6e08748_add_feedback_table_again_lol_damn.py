"""add feedback table again lol damn

Revision ID: a740c6e08748
Revises: a8a96707857e
Create Date: 2025-07-24 21:59:47.223495

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a740c6e08748'
down_revision = 'a8a96707857e'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("sentiment", sa.String(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_feedback_id"), "feedback", ["id"], unique=False)
    op.create_index(op.f("ix_feedback_message"), "feedback", ["message"], unique=False)
    op.create_index(
        op.f("ix_feedback_sentiment"), "feedback", ["sentiment"], unique=False
    )
def downgrade():
    op.drop_index(op.f("ix_feedback_sentiment"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_message"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_id"), table_name="feedback")
    op.drop_table("feedback")
