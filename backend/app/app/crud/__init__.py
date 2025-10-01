from .crud_category import category
from .crud_subcategory import subcategory
from .crud_account import account
from .crud_transfer import transfer
from .crud_income import income
from .crud_expense import expense
from .crud_place import place
from .crud_item import item
from .crud_user import user
from .crud_import import imports
from .crud_feedback import feedback
from .crud_goal import goal

# For a new basic set of CRUD operations you could just do

# from .base import CRUDBase
# from app.models.item import Item
# from app.schemas.item import ItemCreate, ItemUpdate

# item = CRUDBase[Item, ItemCreate, ItemUpdate](Item)
