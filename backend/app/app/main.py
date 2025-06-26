import logging
import secrets

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from Secweb import SecWeb
from starlette.middleware.cors import CORSMiddleware

from app.api.api_v1.api import api_router
from app.api.api_v2.api import api_router as api_router_v2
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.9.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# SecWeb(app=app, Option={'csp': {
#     "default-src": ["'self'"],
#     "img-src": [
#         "'self'",
#         "data:",
#     ],
#     "connect-src": ["'self'"],
#     "script-src": ["'self'"],
#     "style-src": ["'self'", "'unsafe-inline'"],
#     "script-src-elem": [
#         "'self'",
#         "'unsafe-inline'",
#         "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
#     ],
#     "style-src-elem": [
#         "'self'",
#         "'unsafe-inline'",
#         "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
#     ],
#     "base-uri": ["'self'"],
#     "font-src": ["'self'", "https:", "data:"],
#     "frame-ancestors": ["'self'"],
#     "object-src": ["'none'"],
#     "script-src-attr": ["'none'"],
#     "require-trusted-types-for": ["'script'"],
# }
# })

security = HTTPBasic()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Request: {request.method} {request.url} from {request.headers.get('host')}")
    response = await call_next(request)
    logger.info(f"Response status: {response.status_code}")
    return response


# # Set all CORS enabled origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*",
        "https://www.cleverbill.ing",
        "https://cleverbill.ing",
        "https://api.cleverbill.ing",
        "http://api.cleverbill.ing",
        "https://dev.cleverbill.ing",
        "http://dev.cleverbill.ing",
        "https://dev.cleverbill.ing/api/v1",
        "https://dashboard.cleverbill.ing",
        "http://dashboard.cleverbill.ing",
        "https://dashboard.cleverbill.ing/api/v1",
        r"https:\/\/*\.cleverbill\.ing",
        r"https:\/\/*\.cleverbill\.ing/",
        r"http:\/\/*\.cleverbill\.ing",
        r"http:\/\/*\.cleverbill\.ing/",
        "http://localhost:4321",
        "http://localhost",
        "http://localhost:*",
        "http://localhost:3000",
        "http://localhost:8080",
        "https://localhost",
        "https://localhost:*",
        "https://localhost:3000",
        "https://localhost:8080",
        "https://localhost:8888",
        "https://localhost:9000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.DOCS_USER)
    correct_password = secrets.compare_digest(
        credentials.password, settings.DOCS_PASSWORD
    )
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.get("/docs", include_in_schema=False)
async def get_swagger_documentation(username: str = Depends(get_current_username)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")


@app.get("/redoc", include_in_schema=False)
async def get_redoc_documentation(username: str = Depends(get_current_username)):
    return get_redoc_html(openapi_url="/openapi.json", title="docs")


@app.get("/openapi.json", include_in_schema=False)
async def openapi(username: str = Depends(get_current_username)):
    return get_openapi(title=app.title, version=app.version, routes=app.routes)


app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(api_router_v2, prefix=settings.API_V2_STR)
