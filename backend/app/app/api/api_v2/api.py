from fastapi import APIRouter

from app.api.api_v2.endpoints import data

api_router = APIRouter()
api_router.include_router(data.router, prefix="/data", tags=["data"])