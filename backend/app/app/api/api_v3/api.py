from fastapi import APIRouter

from app.api.api_v3.endpoints import accounts, charts, comparison, summary, transactions

api_router = APIRouter()
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
api_router.include_router(
    transactions.router, prefix="/transactions", tags=["transactions"]
)
api_router.include_router(summary.router, prefix="/summary", tags=["summary"])
api_router.include_router(charts.router, prefix="/charts", tags=["charts"])
api_router.include_router(
    comparison.router, prefix="/comparison", tags=["comparison"]
)