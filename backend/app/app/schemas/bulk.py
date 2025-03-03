from typing import Optional, List
from pydantic import BaseModel
from app import schemas


class BulkDelete(BaseModel):
    incomes: Optional[List[int]] = []
    expenses: Optional[List[int]] = []


class BulkDeletionsResponse(BaseModel):
    incomes: List[int] = []
    expenses: List[int] = []

class BulkCreate(BaseModel):
    incomes: Optional[List[schemas.IncomeCreate]] = []
    expenses: Optional[List[schemas.ExpenseCreate]] = []


class BulkCreationsResponse(BaseModel):
    incomes: List[schemas.Income] = []
    expenses: List[schemas.Expense] = []
