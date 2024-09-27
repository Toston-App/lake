from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from app.api.api_v1.api import api_router
from app.api.api_v2.api import api_router as api_router_v2
from app.core.config import settings


import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME, openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Request: {request.method} {request.url}")
    logger.info(f"Headers: {request.headers}")
    response = await call_next(request)
    logger.info(f"Response status: {response.status_code}")
    return response


# Set all CORS enabled origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://api.cleverbill.ing", "https://dev.cleverbill.ing", "http://dev.cleverbill.ing", "https://dev.cleverbill.ing/api/v1", "https:\/\/*\.cleverbill\.ing", "https:\/\/*\.cleverbill\.ing/", "http:\/\/*\.cleverbill\.ing", "http:\/\/*\.cleverbill\.ing/","http://localhost:4321", "http://localhost", "http://localhost:4200", "http://localhost:3000", "http://localhost:8080", "https://localhost", "https://localhost:4200", "https://localhost:3000", "https://localhost:8080", "https://localhost:8888", "https://localhost:9000", "http://dev.wallet.com", "https://stag.wallet.com", "https://wallet.com", "http://local.dockertoolbox.tiangolo.com", "http://localhost.tiangolo.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(api_router_v2, prefix=settings.API_V2_STR)
