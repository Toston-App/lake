from typing import Any, Optional

from pydantic import BaseModel


class Balance(BaseModel):
    total: float
    income: float
    outcome: float


class NetSummary(BaseModel):
    current: float
    previous: float
    change: float
    changePercent: float
    isPositive: bool


class SummaryResponse(BaseModel):
    currency: str
    language: str
    balance: Balance
    period_income: float
    period_expenses: float
    period_net: float


class ChartsResponse(BaseModel):
    net: Any
    income_vs_expense: Any
    categories: Any
    accounts: dict


class ComparisonResponse(BaseModel):
    accounts_growth: dict[str, float]
