"""add recurring transactions

Revision ID: a4c9e2f7b1d3
Revises: d8f1b3a5c7e2
Create Date: 2026-10-21 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4c9e2f7b1d3"
down_revision = "d8f1b3a5c7e2"
branch_labels = None
depends_on = None

TRANSACTION_TABLES = ("expense", "income", "transfer")

recurring_type = postgresql.ENUM(
    "expense", "income", "transfer", name="recurringtransactiontype", create_type=False
)
frequency = postgresql.ENUM(
    "daily",
    "weekly",
    "monthly",
    "semimonthly",
    "yearly",
    name="recurrencefrequency",
    create_type=False,
)
monthly_mode = postgresql.ENUM(
    "day_of_month", "nth_weekday", name="recurrencemonthlymode", create_type=False
)
adjustment = postgresql.ENUM(
    "none", "previous", "next", name="businessdayadjustment", create_type=False
)
ENUMS = (recurring_type, frequency, monthly_mode, adjustment)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "recurring_transaction",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", recurring_type, nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_account_id",
            sa.Integer(),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("category.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "subcategory_id",
            sa.Integer(),
            sa.ForeignKey("subcategory.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "place_id",
            sa.Integer(),
            sa.ForeignKey("place.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("frequency", frequency, nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "weekdays",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "monthly_mode", monthly_mode, nullable=False, server_default="day_of_month"
        ),
        sa.Column(
            "month_days",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("week_of_month", sa.Integer(), nullable=True),
        sa.Column(
            "business_day_adjustment",
            adjustment,
            nullable=False,
            server_default="none",
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("max_occurrences", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "occurrences_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("next_nominal_date", sa.Date(), nullable=True),
        sa.Column("next_run_date", sa.Date(), nullable=True),
        sa.Column("last_run_date", sa.Date(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_recurring_transaction_id", "recurring_transaction", ["id"])
    op.create_index(
        "ix_recurring_transaction_owner_id", "recurring_transaction", ["owner_id"]
    )
    op.create_index(
        "ix_recurring_transaction_due",
        "recurring_transaction",
        ["next_run_date"],
        postgresql_where=sa.text("is_active AND next_run_date IS NOT NULL"),
    )

    for table in TRANSACTION_TABLES:
        op.add_column(table, sa.Column("recurring_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"{table}_recurring_id_fkey",
            table,
            "recurring_transaction",
            ["recurring_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table}_recurring_id", table, ["recurring_id"])


def downgrade() -> None:
    for table in TRANSACTION_TABLES:
        op.drop_index(f"ix_{table}_recurring_id", table_name=table)
        op.drop_constraint(f"{table}_recurring_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "recurring_id")

    op.drop_index("ix_recurring_transaction_due", table_name="recurring_transaction")
    op.drop_index(
        "ix_recurring_transaction_owner_id", table_name="recurring_transaction"
    )
    op.drop_index("ix_recurring_transaction_id", table_name="recurring_transaction")
    op.drop_table("recurring_transaction")

    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind, checkfirst=True)
