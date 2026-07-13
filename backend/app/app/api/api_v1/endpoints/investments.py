"""
Investment Dashboard - Main router that aggregates all investment endpoints.
"""
from fastapi import APIRouter, Depends

from app.api import deps
from app.api.api_v1.endpoints import assets, holdings, investment_transactions, portfolio

router = APIRouter(dependencies=[Depends(deps.require_investments_access)])

# Include all investment sub-routers
router.include_router(assets.router, prefix="/assets", tags=["investments-assets"])
router.include_router(holdings.router, prefix="/holdings", tags=["investments-holdings"])
router.include_router(
    investment_transactions.router, 
    prefix="/transactions", 
    tags=["investments-transactions"]
)
router.include_router(portfolio.router, prefix="/portfolio", tags=["investments-portfolio"])
