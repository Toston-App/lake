"""harden investment integrity

Revision ID: 6f45d8c2a901
Revises: af1c276ebdd1
"""

from alembic import op


revision = "6f45d8c2a901"
down_revision = "af1c276ebdd1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_asset_coingecko_id", "asset", ["coingecko_id"])
    op.create_unique_constraint(
        "uq_holding_account_asset", "holding", ["account_id", "asset_id"]
    )
    op.create_check_constraint(
        "ck_holding_quantity_nonnegative", "holding", "quantity >= 0"
    )
    op.create_check_constraint(
        "ck_holding_cost_nonnegative", "holding", "avg_cost_basis >= 0"
    )
    op.create_check_constraint(
        "ck_holding_total_nonnegative", "holding", "total_invested >= 0"
    )
    op.create_check_constraint("ck_holding_quantity_max", "holding", "quantity <= 1e15")
    op.create_check_constraint("ck_holding_cost_max", "holding", "avg_cost_basis <= 1e15")
    op.create_check_constraint("ck_holding_total_max", "holding", "total_invested <= 1e30")
    op.create_check_constraint(
        "ck_holding_current_value_range",
        "holding",
        "current_value >= 0 AND current_value <= 1e30",
    )
    op.create_check_constraint(
        "ck_holding_current_value_usd_range",
        "holding",
        "current_value_usd >= 0 AND current_value_usd <= 1e30",
    )
    op.create_check_constraint(
        "ck_holding_current_value_mxn_range",
        "holding",
        "current_value_mxn >= 0 AND current_value_mxn <= 1e30",
    )
    op.create_check_constraint(
        "ck_investment_tx_quantity_positive", "investmenttransaction", "quantity > 0"
    )
    op.create_check_constraint(
        "ck_investment_tx_price_nonnegative",
        "investmenttransaction",
        "price_per_unit >= 0",
    )
    op.create_check_constraint(
        "ck_investment_tx_fees_nonnegative", "investmenttransaction", "fees >= 0"
    )
    op.create_check_constraint(
        "ck_investment_tx_quantity_max", "investmenttransaction", "quantity <= 1e15"
    )
    op.create_check_constraint(
        "ck_investment_tx_price_max", "investmenttransaction", "price_per_unit <= 1e15"
    )
    op.create_check_constraint(
        "ck_investment_tx_fees_max", "investmenttransaction", "fees <= 1e15"
    )
    op.create_check_constraint(
        "ck_investment_tx_total_nonnegative", "investmenttransaction", "total_amount >= 0"
    )
    op.create_check_constraint(
        "ck_investment_tx_total_max", "investmenttransaction", "total_amount <= 1e30"
    )
    op.create_check_constraint(
        "ck_investment_tx_usd_rate_positive",
        "investmenttransaction",
        "exchange_rate_to_usd IS NULL OR (exchange_rate_to_usd > 0 AND exchange_rate_to_usd <= 1e6)",
    )
    op.create_check_constraint(
        "ck_investment_tx_mxn_rate_positive",
        "investmenttransaction",
        "exchange_rate_to_mxn IS NULL OR (exchange_rate_to_mxn > 0 AND exchange_rate_to_mxn <= 1e6)",
    )
    op.create_check_constraint(
        "ck_asset_price_native_range", "assetprice", "price > 0 AND price <= 1e15"
    )
    op.create_check_constraint(
        "ck_asset_price_usd_range", "assetprice", "price_usd > 0 AND price_usd <= 1e15"
    )
    op.create_check_constraint(
        "ck_asset_price_mxn_range", "assetprice", "price_mxn > 0 AND price_mxn <= 1e15"
    )
    op.create_check_constraint(
        "ck_asset_price_volume_nonnegative",
        "assetprice",
        "volume IS NULL OR volume >= 0",
    )


def downgrade():
    op.drop_constraint("ck_asset_price_volume_nonnegative", "assetprice", type_="check")
    op.drop_constraint("ck_asset_price_mxn_range", "assetprice", type_="check")
    op.drop_constraint("ck_asset_price_usd_range", "assetprice", type_="check")
    op.drop_constraint("ck_asset_price_native_range", "assetprice", type_="check")
    op.drop_constraint(
        "ck_investment_tx_mxn_rate_positive", "investmenttransaction", type_="check"
    )
    op.drop_constraint(
        "ck_investment_tx_usd_rate_positive", "investmenttransaction", type_="check"
    )
    op.drop_constraint(
        "ck_investment_tx_fees_nonnegative", "investmenttransaction", type_="check"
    )
    op.drop_constraint(
        "ck_investment_tx_price_nonnegative", "investmenttransaction", type_="check"
    )
    op.drop_constraint(
        "ck_investment_tx_quantity_positive", "investmenttransaction", type_="check"
    )
    op.drop_constraint("ck_investment_tx_total_max", "investmenttransaction", type_="check")
    op.drop_constraint(
        "ck_investment_tx_total_nonnegative", "investmenttransaction", type_="check"
    )
    op.drop_constraint("ck_investment_tx_fees_max", "investmenttransaction", type_="check")
    op.drop_constraint("ck_investment_tx_price_max", "investmenttransaction", type_="check")
    op.drop_constraint("ck_investment_tx_quantity_max", "investmenttransaction", type_="check")
    op.drop_constraint("ck_holding_current_value_mxn_range", "holding", type_="check")
    op.drop_constraint("ck_holding_current_value_usd_range", "holding", type_="check")
    op.drop_constraint("ck_holding_current_value_range", "holding", type_="check")
    op.drop_constraint("ck_holding_total_max", "holding", type_="check")
    op.drop_constraint("ck_holding_cost_max", "holding", type_="check")
    op.drop_constraint("ck_holding_quantity_max", "holding", type_="check")
    op.drop_constraint("ck_holding_total_nonnegative", "holding", type_="check")
    op.drop_constraint("ck_holding_cost_nonnegative", "holding", type_="check")
    op.drop_constraint("ck_holding_quantity_nonnegative", "holding", type_="check")
    op.drop_constraint("uq_holding_account_asset", "holding", type_="unique")
    op.drop_constraint("uq_asset_coingecko_id", "asset", type_="unique")
