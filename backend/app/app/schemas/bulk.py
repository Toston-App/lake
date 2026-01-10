from typing import Optional

from pydantic import BaseModel

from app.schemas.expense import Expense, ExpenseCreate
from app.schemas.income import Income, IncomeCreate


class BulkDelete(BaseModel):
    incomes: Optional[list[int]] = []
    expenses: Optional[list[int]] = []


class BulkDeletionsResponse(BaseModel):
    incomes: list[int] = []
    expenses: list[int] = []


class BulkCreate(BaseModel):
    incomes: Optional[list[IncomeCreate]] = []
    expenses: Optional[list[ExpenseCreate]] = []


class BulkCreationsResponse(BaseModel):
    incomes: list[Income] = []
    expenses: list[Expense] = []
