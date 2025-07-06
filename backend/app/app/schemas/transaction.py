from datetime import date
from typing import Literal, Optional, Union

from pydantic import BaseModel


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
        orm_mode = True


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
        orm_mode = True


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
        orm_mode = True


# Transaction = Union[ExpenseTransaction]
Transaction = Union[ExpenseTransaction, IncomeTransaction, TransferTransaction]
