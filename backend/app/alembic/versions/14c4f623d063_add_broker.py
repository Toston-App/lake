"""add broker

Revision ID: 14c4f623d063
Revises: ad6c96956dc4
Create Date: 2026-01-22 17:33:04.617996

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '14c4f623d063'
down_revision = 'ad6c96956dc4'
branch_labels = None
depends_on = None



# All broker enum values
BROKER_VALUES = [
    'FIDELITY', 'SCHWAB', 'VANGUARD', 'ETRADE', 'TD_AMERITRADE',
    'MERRILL', 'MORGAN_STANLEY', 'JP_MORGAN', 'ROBINHOOD', 'WEBULL',
    'SOFI', 'M1_FINANCE', 'ALLY', 'TRADESTATION', 'FIRSTRADE',
    'PUBLIC', 'TASTYTRADE', 'GBM', 'KUSPIT', 'ACTINVER', 'BURSANET',
    'BBVA_TRADER', 'FINAMEX', 'BANORTE', 'VECTOR', 'INTERCAM', 'MONEX',
    'COINBASE', 'BINANCE', 'KRAKEN', 'GEMINI', 'CRYPTO_COM', 'BITSO',
    'KUCOIN', 'BITSTAMP', 'OKX', 'BYBIT', 'IBKR', 'DEGIRO', 'ETORO',
    'TRADING_212', 'SAXO', 'SWISSQUOTE', 'CMC', 'IG'
]


def upgrade():
    # Create the broker enum type
    broker_enum = sa.Enum(*BROKER_VALUES, name='broker')
    broker_enum.create(op.get_bind(), checkfirst=True)
    
    # Drop the old string column
    op.drop_column('investmenttransaction', 'broker')
    
    # Add new enum column
    op.add_column(
        'investmenttransaction',
        sa.Column('broker', sa.Enum(*BROKER_VALUES, name='broker'), nullable=True)
    )


def downgrade():
    # Drop the enum column
    op.drop_column('investmenttransaction', 'broker')
    
    # Add back the string column
    op.add_column(
        'investmenttransaction',
        sa.Column('broker', sa.String(), nullable=True)
    )
    
    # Drop the enum type
    broker_enum = sa.Enum(*BROKER_VALUES, name='broker')
    broker_enum.drop(op.get_bind(), checkfirst=True)
