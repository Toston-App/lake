from typing import Optional

from pydantic import BaseModel

from app import schemas


class BulkDelete(BaseModel):
    incomes: Optional[list[int]] = []
    expenses: Optional[list[int]] = []


class BulkDeletionsResponse(BaseModel):
    incomes: list[int] = []
    expenses: list[int] = []


class BulkCreate(BaseModel):
    incomes: Optional[list[schemas.IncomeCreate]] = []
    expenses: Optional[list[schemas.ExpenseCreate]] = []


class BulkCreationsResponse(BaseModel):
    incomes: list[schemas.Income] = []
    expenses: list[schemas.Expense] = []
