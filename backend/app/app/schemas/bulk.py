from pydantic import BaseModel

from app import schemas


class BulkDelete(BaseModel):
    incomes: list[int] | None = []
    expenses: list[int] | None = []


class BulkDeletionsResponse(BaseModel):
    incomes: list[int] = []
    expenses: list[int] = []


class BulkCreate(BaseModel):
    incomes: list[schemas.IncomeCreate] | None = []
    expenses: list[schemas.ExpenseCreate] | None = []


class BulkCreationsResponse(BaseModel):
    incomes: list[schemas.Income] = []
    expenses: list[schemas.Expense] = []
