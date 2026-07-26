# Import all the models, so that Base has them before being
# imported by Alembic
from app.db.base_class import Base  # noqa
from app.models.account import Account  # noqa
from app.models.asset import Asset  # noqa
from app.models.asset_price import AssetPrice  # noqa
from app.models.holding import Holding  # noqa
from app.models.investment_transaction import InvestmentTransaction  # noqa
from app.models.item import Item  # noqa
from app.models.user import User  # noqa
