"""add performance indexes

Revision ID: perf_idx_001
Revises: fa7a11b93453
Create Date: 2025-09-05 20:53:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'perf_idx_001'
down_revision = 'fa7a11b93453'
branch_labels = None
depends_on = None


def upgrade():
    # Add indexes for frequently queried fields
    
    # Expense table indexes
    op.create_index('idx_expense_owner_id', 'expense', ['owner_id'])
    op.create_index('idx_expense_date', 'expense', ['date'])
    op.create_index('idx_expense_category_id', 'expense', ['category_id'])
    op.create_index('idx_expense_subcategory_id', 'expense', ['subcategory_id'])
    op.create_index('idx_expense_account_id', 'expense', ['account_id'])
    op.create_index('idx_expense_place_id', 'expense', ['place_id'])
    op.create_index('idx_expense_owner_date', 'expense', ['owner_id', 'date'])
    
    # Income table indexes
    op.create_index('idx_income_owner_id', 'income', ['owner_id'])
    op.create_index('idx_income_date', 'income', ['date'])
    op.create_index('idx_income_subcategory_id', 'income', ['subcategory_id'])
    op.create_index('idx_income_account_id', 'income', ['account_id'])
    op.create_index('idx_income_place_id', 'income', ['place_id'])
    op.create_index('idx_income_owner_date', 'income', ['owner_id', 'date'])
    
    # Transfer table indexes
    op.create_index('idx_transfer_owner_id', 'transfer', ['owner_id'])
    op.create_index('idx_transfer_date', 'transfer', ['date'])
    op.create_index('idx_transfer_from_acc', 'transfer', ['from_acc'])
    op.create_index('idx_transfer_to_acc', 'transfer', ['to_acc'])
    op.create_index('idx_transfer_owner_date', 'transfer', ['owner_id', 'date'])
    
    # Account table indexes
    op.create_index('idx_account_owner_id', 'account', ['owner_id'])
    
    # Category table indexes
    op.create_index('idx_category_owner_id', 'category', ['owner_id'])
    
    # Subcategory table indexes
    op.create_index('idx_subcategory_owner_id', 'subcategory', ['owner_id'])
    op.create_index('idx_subcategory_category_id', 'subcategory', ['category_id'])
    
    # Place table indexes
    op.create_index('idx_place_name', 'place', ['name'])


def downgrade():
    # Remove indexes
    op.drop_index('idx_expense_owner_id')
    op.drop_index('idx_expense_date')
    op.drop_index('idx_expense_category_id')
    op.drop_index('idx_expense_subcategory_id')
    op.drop_index('idx_expense_account_id')
    op.drop_index('idx_expense_place_id')
    op.drop_index('idx_expense_owner_date')
    
    op.drop_index('idx_income_owner_id')
    op.drop_index('idx_income_date')
    op.drop_index('idx_income_subcategory_id')
    op.drop_index('idx_income_account_id')
    op.drop_index('idx_income_place_id')
    op.drop_index('idx_income_owner_date')
    
    op.drop_index('idx_transfer_owner_id')
    op.drop_index('idx_transfer_date')
    op.drop_index('idx_transfer_from_acc')
    op.drop_index('idx_transfer_to_acc')
    op.drop_index('idx_transfer_owner_date')
    
    op.drop_index('idx_account_owner_id')
    op.drop_index('idx_category_owner_id')
    op.drop_index('idx_subcategory_owner_id')
    op.drop_index('idx_subcategory_category_id')
    op.drop_index('idx_place_name')