# ruff: noqa: F401

from .crud_account import account
from .crud_asset import asset
from .crud_asset_price import asset_price
from .crud_balance_adjustment import balance_adjustment
from .crud_category import category
from .crud_expense import expense
from .crud_feedback import feedback
from .crud_holding import holding
from .crud_import import imports
from .crud_income import income
from .crud_investment_transaction import investment_transaction
from .crud_item import item
from .crud_place import place
from .crud_subcategory import subcategory
from .crud_transfer import transfer
from .crud_user import user

# For a new basic set of CRUD operations you could just do

# from .base import CRUDBase
# from app.models.item import Item
# from app.schemas.item import ItemCreate, ItemUpdate

# item = CRUDBase[Item, ItemCreate, ItemUpdate](Item)
