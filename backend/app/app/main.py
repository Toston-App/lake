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

print("🚀 ~ settings.BACKEND_CORS_ORIGINS:", settings.BACKEND_CORS_ORIGINS)
print([str(origin).strip("/") for origin in settings.BACKEND_CORS_ORIGINS])

# Set all CORS enabled origins
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).strip("/") for origin in settings.BACKEND_CORS_ORIGINS] + ["http://localhost:4321", "https://dev.cleverbill.ing"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(api_router_v2, prefix=settings.API_V2_STR)
