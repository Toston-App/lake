import secrets
from typing import Any, Optional

from pydantic import AnyHttpUrl, EmailStr, HttpUrl, PostgresDsn
from pydantic_settings import BaseSettings
from pydantic import field_validator


class AsyncPostgresDsn(PostgresDsn):
    allowed_schemes = {"postgres+asyncpg", "postgresql+asyncpg"}


class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    API_V2_STR: str = "/api/v2"
    # used for jwt
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # used for encryption with Fernet
    ENCRYPTION_KEY: str

    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    SERVER_NAME: str
    SERVER_HOST: AnyHttpUrl
    TEST_MODE: bool = False
    PROFILE_QUERY_MODE: bool = False

    PROJECT_NAME: str
    SENTRY_DSN: Optional[HttpUrl] = None

    @field_validator("SENTRY_DSN", mode='before')
    def sentry_dsn_can_be_blank(cls, v: str) -> Optional[str]:
        if len(v) == 0:
            return None
        return v

    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    SQLALCHEMY_DATABASE_URI: Optional[PostgresDsn] = None
    SQLALCHEMY_DATABASE_URI_ASYNC: Optional[AsyncPostgresDsn] = None

    @field_validator("POSTGRES_DB", mode='before')
    def assemble_db_name(cls, v: Optional[str], info: dict[str, Any]) -> Any:
        if info.data.get("TEST_MODE"):
            return "postgres"
        if isinstance(v, str):
            return v

    @field_validator("SQLALCHEMY_DATABASE_URI", mode='before')
    def assemble_db_connection(cls, v: Optional[str], info: dict[str, Any]) -> Any:
        if isinstance(v, str):
            return v
        return PostgresDsn.build(
            scheme="postgresql",
            username=info.data.get("POSTGRES_USER"),
            password=info.data.get("POSTGRES_PASSWORD"),
            host=info.data.get("POSTGRES_SERVER"),
            path=f"{info.data.get('POSTGRES_DB') or ''}",
        )

    @field_validator("SQLALCHEMY_DATABASE_URI_ASYNC", mode='before')
    def assemble_async_db_connection(
        cls, v: Optional[str], info: dict[str, Any]
    ) -> Any:
        if isinstance(v, str):
            return v
        return AsyncPostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=info.data.get("POSTGRES_USER"),
            password=info.data.get("POSTGRES_PASSWORD"),
            host=info.data.get("POSTGRES_SERVER"),
            path=f"{info.data.get('POSTGRES_DB') or ''}",
        )

    SMTP_TLS: bool = True
    SMTP_PORT: Optional[int] = None
    SMTP_HOST: Optional[str] = None
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAILS_FROM_EMAIL: Optional[EmailStr] = None
    EMAILS_FROM_NAME: Optional[str] = None

    @field_validator("EMAILS_FROM_NAME")
    def get_project_name(cls, v: Optional[str], info: dict[str, Any]) -> str:
        if not v:
            return info.data["PROJECT_NAME"]
        return v

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48
    EMAIL_TEMPLATES_DIR: str = "/app/email-templates/build"
    EMAILS_ENABLED: bool = False

    @field_validator("EMAILS_ENABLED", mode='before')
    def get_emails_enabled(cls, v: bool, info: dict[str, Any]) -> bool:
        return bool(
            info.data.get("SMTP_HOST")
            and info.data.get("SMTP_PORT")
            and info.data.get("EMAILS_FROM_EMAIL")
        )

    EMAIL_TEST_USER: EmailStr = "test@example.com"  # type: ignore
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str
    USERS_OPEN_REGISTRATION: bool = False

    SEED_DATABASE: bool = False

    SERVER_NAME: str = "localhost"
    SERVER_HOST: str = "http://localhost"

    DOCS_USER: str = "user"
    DOCS_PASSWORD: str = "password"

    OPENAI_API_KEY: str

    # Feedback
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_OWNER_ID: str

    # WhatsApp Integration Settings
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_ACCESS_TOKEN: str
    WHATSAPP_PHONE_NUMBER_ID: Optional[str] = None
    WHATSAPP_VERIFY_TOKEN: Optional[str] = None
    WHATSAPP_API_VERSION: str = "v22.0"

    # WAHA
    WAHA_SESSION: str
    WAHA_URL: str
    WHATSAPP_API_KEY: str

    REDIS_URL: str
    REDIS_TOKEN: str

    @field_validator("WHATSAPP_ENABLED", mode='before')
    def get_whatsapp_enabled(cls, v: bool, info: dict[str, Any]) -> bool:
        return bool(
            info.data.get("WHATSAPP_ACCESS_TOKEN")
            and info.data.get("WHATSAPP_PHONE_NUMBER_ID")
            and bool(info.data.get("WHATSAPP_VERIFY_TOKEN"))
        )

    class Config:
        case_sensitive = True


settings = Settings()
