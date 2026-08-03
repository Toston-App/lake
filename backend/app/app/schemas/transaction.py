from datetime import date
from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel

from app.schemas.account import Account
from app.schemas.category import Category
from app.schemas.place import Place
from app.schemas.subcategory import Subcategory


class TransactionType(str, Enum):
    expense = "expense"
    income = "income"
    transfer = "transfer"


class AmountOperator(str, Enum):
    equal = "equal"
    less = "less"
    greater = "greater"


class OrderDirection(str, Enum):
    asc = "asc"
    desc = "desc"


class ExpenseTransaction(BaseModel):
    id: int
    amount: float
    date: Optional[date]
    description: Optional[str]
    type: Literal["expense"] = "expense"

    owner_id: int
    account_id: Optional[int]
    category_id: Optional[int]
    subcategory_id: Optional[int]
    place_id: Optional[int]

    class Config:
        from_attributes = True


class IncomeTransaction(BaseModel):
    id: int
    amount: float
    date: Optional[date]
    description: Optional[str]
    type: Literal["income"] = "income"

    owner_id: int
    account_id: Optional[int]
    subcategory_id: Optional[int]
    place_id: Optional[int]

    class Config:
        from_attributes = True


class TransferTransaction(BaseModel):
    id: int
    amount: float
    date: Optional[date]
    description: Optional[str]
    type: Literal["transfer"] = "transfer"

    owner_id: int
    from_acc: Optional[int]
    to_acc: Optional[int]

    class Config:
        from_attributes = True


Transaction = Union[ExpenseTransaction, IncomeTransaction, TransferTransaction]
