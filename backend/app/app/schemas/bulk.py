from typing import Optional, List
from pydantic import BaseModel


class BulkDelete(BaseModel):
    incomes: Optional[List[int]] = []
    expenses: Optional[List[int]] = []


class BulkDeletionsResponse(BaseModel):
    incomes: List[int] = []
    expenses: List[int] = []