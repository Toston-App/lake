"""Harden investment storage and request safety.

Revision ID: 8d3c4f1a2b90
Revises: 6f45d8c2a901
"""

import sqlalchemy as sa
from alembic import op

revision = "8d3c4f1a2b90"
down_revision = "6f45d8c2a901"
branch_labels = None
depends_on = None


MONEY_COLUMNS = {
    "holding": (
        "avg_cost_basis",
        "total_invested",
        "current_value",
        "current_value_mxn",
        "current_value_usd",
        "unrealized_gain_loss",
    ),
    "investmenttransaction": ("price_per_unit", "total_amount", "fees"),
    "assetprice": (
        "price",
        "price_usd",
        "price_mxn",
        "open_price",
        "high_price",
        "low_price",
        "previous_close",
        "volume",
        "change",
    ),
}


def _alter_numeric(table: str, column: str, numeric: sa.Numeric) -> None:
    op.alter_column(
        table,
        column,
        existing_type=sa.Float(),
        type_=numeric,
        postgresql_using=f"{column}::numeric",
    )


def upgrade() -> None:
    _alter_numeric("holding", "quantity", sa.Numeric(28, 12))
    _alter_numeric("investmenttransaction", "quantity", sa.Numeric(28, 12))
    for table, columns in MONEY_COLUMNS.items():
        for column in columns:
            _alter_numeric(table, column, sa.Numeric(38, 8))
    _alter_numeric("holding", "unrealized_gain_loss_pct", sa.Numeric(20, 12))
    _alter_numeric("investmenttransaction", "exchange_rate_to_usd", sa.Numeric(20, 12))
    _alter_numeric("investmenttransaction", "exchange_rate_to_mxn", sa.Numeric(20, 12))
    _alter_numeric("assetprice", "change_percent", sa.Numeric(20, 12))

    op.add_column(
        "account",
        sa.Column(
            "total_investments_usd",
            sa.Numeric(38, 8),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "account",
        sa.Column(
            "total_investments_mxn",
            sa.Numeric(38, 8),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """
        UPDATE account
        SET total_investments_usd = COALESCE(holding_totals.usd, 0),
            total_investments_mxn = COALESCE(holding_totals.mxn, 0)
        FROM (
            SELECT account_id,
                   SUM(current_value_usd) AS usd,
                   SUM(current_value_mxn) AS mxn
            FROM holding
            GROUP BY account_id
        ) AS holding_totals
        WHERE account.id = holding_totals.account_id
        """
    )
    op.create_index(
        "ix_account_total_investments_usd",
        "account",
        ["total_investments_usd"],
    )
    op.create_index(
        "ix_account_total_investments_mxn",
        "account",
        ["total_investments_mxn"],
    )
    op.drop_index("ix_account_total_investments", table_name="account")
    op.drop_column("account", "total_investments")

    op.add_column(
        "investmenttransaction",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "investmenttransaction",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_investment_tx_owner_idempotency",
        "investmenttransaction",
        ["owner_id", "idempotency_key"],
    )
    op.create_index(
        "ix_assetprice_asset_fetched_at",
        "assetprice",
        ["asset_id", "fetched_at"],
    )
    op.create_index(
        "ix_investmenttransaction_owner_executed_at",
        "investmenttransaction",
        ["owner_id", "executed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_investmenttransaction_owner_executed_at",
        table_name="investmenttransaction",
    )
    op.drop_index("ix_assetprice_asset_fetched_at", table_name="assetprice")
    op.drop_constraint(
        "uq_investment_tx_owner_idempotency",
        "investmenttransaction",
        type_="unique",
    )
    op.drop_column("investmenttransaction", "request_fingerprint")
    op.drop_column("investmenttransaction", "idempotency_key")

    op.add_column(
        "account",
        sa.Column("total_investments", sa.Float(), nullable=True),
    )
    op.create_index("ix_account_total_investments", "account", ["total_investments"])
    op.drop_index("ix_account_total_investments_mxn", table_name="account")
    op.drop_index("ix_account_total_investments_usd", table_name="account")
    op.drop_column("account", "total_investments_mxn")
    op.drop_column("account", "total_investments_usd")

    op.alter_column(
        "assetprice",
        "change_percent",
        existing_type=sa.Numeric(20, 12),
        type_=sa.Float(),
        postgresql_using="change_percent::double precision",
    )
    op.alter_column(
        "investmenttransaction",
        "exchange_rate_to_mxn",
        existing_type=sa.Numeric(20, 12),
        type_=sa.Float(),
        postgresql_using="exchange_rate_to_mxn::double precision",
    )
    op.alter_column(
        "investmenttransaction",
        "exchange_rate_to_usd",
        existing_type=sa.Numeric(20, 12),
        type_=sa.Float(),
        postgresql_using="exchange_rate_to_usd::double precision",
    )
    op.alter_column(
        "holding",
        "unrealized_gain_loss_pct",
        existing_type=sa.Numeric(20, 12),
        type_=sa.Float(),
        postgresql_using="unrealized_gain_loss_pct::double precision",
    )
    for table, columns in MONEY_COLUMNS.items():
        for column in columns:
            op.alter_column(
                table,
                column,
                existing_type=sa.Numeric(38, 8),
                type_=sa.Float(),
                postgresql_using=f"{column}::double precision",
            )
    for table in ("holding", "investmenttransaction"):
        op.alter_column(
            table,
            "quantity",
            existing_type=sa.Numeric(28, 12),
            type_=sa.Float(),
            postgresql_using="quantity::double precision",
        )
