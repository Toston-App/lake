from pydantic import BaseModel

from app.schemas.expense import Expense, ExpenseCreate
from app.schemas.income import Income, IncomeCreate


class BulkDelete(BaseModel):
    incomes: list[int] | None = []
    expenses: list[int] | None = []


class BulkDeletionsResponse(BaseModel):
    incomes: list[int] = []
    expenses: list[int] = []


class BulkCreate(BaseModel):
    incomes: list[IncomeCreate] | None = []
    expenses: list[ExpenseCreate] | None = []


class BulkCreationsResponse(BaseModel):
    incomes: list[Income] = []
    expenses: list[Expense] = []
