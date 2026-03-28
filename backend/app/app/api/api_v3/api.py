from fastapi import APIRouter

from app.api.api_v3.endpoints import charts, comparison, summary, transactions

api_router = APIRouter()
api_router.include_router(summary.router, prefix="/summary", tags=["summary"])
api_router.include_router(charts.router, prefix="/charts", tags=["charts"])
# Disabled since this is not used for v3 migration but could be helpful for future features. If enabled, re check implementation is good and secure.
# api_router.include_router(
#     comparison.router, prefix="/comparison", tags=["comparison"]
# )