from datetime import date
from typing import Literal, Optional, Union

from pydantic import BaseModel

from app.schemas.account import Account
from app.schemas.category import Category
from app.schemas.place import Place
from app.schemas.subcategory import Subcategory


class ExpenseTransaction(BaseModel):
    id: int
    amount: float
    date: date
    description: Optional[str]
    type: Literal["expense"] = "expense"

    owner_id: int
    account_id: Optional[int]
    account: Optional[Account]

    category_id: Optional[int]
    category: Optional[Category]
    subcategory_id: Optional[int]
    subcategory: Optional[Subcategory]
    place_id: Optional[int]
    place: Optional[Place]

    class Config:
        orm_mode = True


class IncomeTransaction(BaseModel):
    id: int
    amount: float
    date: date
    description: Optional[str]
    type: Literal["income"] = "income"

    owner_id: int
    account_id: Optional[int]
    account: Optional[Account]
    subcategory_id: Optional[int]
    subcategory: Optional[Subcategory]
    place_id: Optional[int]
    place: Optional[Place]

    class Config:
        orm_mode = True


class TransferTransaction(BaseModel):
    id: int
    amount: float
    date: date
    description: Optional[str]
    type: Literal["transfer"] = "transfer"

    owner_id: int
    from_acc: Optional[int]
    account_from: Optional[Account]
    to_acc: Optional[int]
    account_to: Optional[Account]

    class Config:
        orm_mode = True


# Transaction = Union[ExpenseTransaction]
Transaction = Union[ExpenseTransaction, IncomeTransaction, TransferTransaction]
