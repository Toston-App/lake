from typing import Annotated, Any

from pydantic import (
    AnyHttpUrl,
    EmailStr,
    HttpUrl,
    PostgresDsn,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode


class AsyncPostgresDsn(PostgresDsn):
    allowed_schemes = {"postgres+asyncpg", "postgresql+asyncpg"}


class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    API_V2_STR: str = "/api/v2"
    API_V3_STR: str = "/api/v3"

    # HS256 signing secret for our own (email/password) access tokens
    # and password-reset tokens. MUST be set; no insecure default.
    LOCAL_JWT_SECRET: str

    # Clerk JWT verification (RS256). The public key is base64-encoded PEM,
    # CLERK_ISSUER is the full issuer URL (e.g. https://your-app.clerk.accounts.dev),
    # and CLERK_AUTHORIZED_PARTIES is the allow-list of acceptable `azp` values
    # (your frontend origins). Accepts either a JSON array or a comma-separated
    # string from the environment.
    CLERK_JWT_PUBLIC_KEY: str
    CLERK_ISSUER: str
    # Backend API secret used to retrieve Clerk user profiles when a local
    # UUID-only user needs an email address for billing.
    CLERK_SECRET_KEY: str | None = None
    # NoDecode tells pydantic-settings not to JSON-parse this field's raw env
    # value, so our `mode="before"` validator below sees the original string
    # and can accept either a JSON array or a comma-separated list.
    CLERK_AUTHORIZED_PARTIES: Annotated[list[str], NoDecode] = []

    @field_validator("CLERK_AUTHORIZED_PARTIES", mode="before")
    def split_authorized_parties(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                import json

                return json.loads(v)
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("LOCAL_JWT_SECRET", "CLERK_JWT_PUBLIC_KEY", "CLERK_ISSUER")
    def _require_non_empty(cls, v: str, info) -> str:
        # Fail-fast on empty strings. Without this, pydantic happily loads
        # `FOO=` from .env as the empty string and the auth code blows up
        # later in obscure ways (broken JWT signature checks, invalid
        # base64, etc.).
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} must be set to a non-empty value.")
        return v

    # used for encryption with Fernet
    ENCRYPTION_KEY: str

    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    SERVER_NAME: str = "localhost"
    SERVER_HOST: AnyHttpUrl = AnyHttpUrl("http://localhost")
    PROFILE_QUERY_MODE: bool = False

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None

    @field_validator("SENTRY_DSN", mode="before")
    def sentry_dsn_can_be_blank(cls, v: str | None) -> str | None:
        if v is None or len(v) == 0:
            return None
        return v

    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    SQLALCHEMY_DATABASE_URI: PostgresDsn | None = None
    SQLALCHEMY_DATABASE_URI_ASYNC: AsyncPostgresDsn | None = None

    @field_validator("SQLALCHEMY_DATABASE_URI", mode="before")
    def assemble_db_connection(cls, v: str | None, info: dict[str, Any]) -> Any:
        if isinstance(v, str):
            return v
        return PostgresDsn.build(
            scheme="postgresql",
            username=info.data.get("POSTGRES_USER"),
            password=info.data.get("POSTGRES_PASSWORD"),
            host=info.data.get("POSTGRES_SERVER"),
            path=f"{info.data.get('POSTGRES_DB') or ''}",
        )

    @field_validator("SQLALCHEMY_DATABASE_URI_ASYNC", mode="before")
    def assemble_async_db_connection(cls, v: str | None, info: dict[str, Any]) -> Any:
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
    SMTP_PORT: int | None = None
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @field_validator("EMAILS_FROM_NAME")
    def get_project_name(cls, v: str | None, info: dict[str, Any]) -> str | None:
        if not v:
            return info.data.get("PROJECT_NAME")
        return v

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48
    EMAIL_TEMPLATES_DIR: str = "/app/app/email-templates/build"
    EMAILS_ENABLED: bool = False

    @field_validator("EMAILS_ENABLED", mode="before")
    def get_emails_enabled(cls, v: bool, info: dict[str, Any]) -> bool:
        return bool(
            info.data.get("SMTP_HOST")
            and info.data.get("SMTP_PORT")
            and info.data.get("EMAILS_FROM_EMAIL")
        )

    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str
    USERS_OPEN_REGISTRATION: bool = False

    # Investment feature access
    INVESTMENTS_ENABLED: bool = False
    INVESTMENTS_ALLOWED_USER_IDS: str = ""
    INVESTMENTS_ALLOWED_USER_UUIDS: str = ""

    @field_validator("INVESTMENTS_ALLOWED_USER_IDS")
    def validate_investment_user_ids(cls, v: str) -> str:
        for raw_id in v.split(","):
            raw_id = raw_id.strip()
            if not raw_id:
                continue
            try:
                user_id = int(raw_id)
            except ValueError as exc:
                raise ValueError(
                    "INVESTMENTS_ALLOWED_USER_IDS must contain comma-separated integers"
                ) from exc
            if user_id <= 0:
                raise ValueError(
                    "INVESTMENTS_ALLOWED_USER_IDS must contain positive integers"
                )
        return v

    @property
    def investments_allowed_user_ids(self) -> set[int]:
        return {
            int(raw_id.strip())
            for raw_id in self.INVESTMENTS_ALLOWED_USER_IDS.split(",")
            if raw_id.strip()
        }

    @property
    def investments_allowed_user_uuids(self) -> set[str]:
        return {
            raw_uuid.strip()
            for raw_uuid in self.INVESTMENTS_ALLOWED_USER_UUIDS.split(",")
            if raw_uuid.strip()
        }

    SEED_DATABASE: bool = False

    DOCS_USER: str = "user"
    DOCS_PASSWORD: str = "password"

    OPENAI_API_KEY: str

    # OpenRouter Settings (for WhatsApp AI parsing)
    OPENROUTER_API_KEY: str
    OPENROUTER_MODEL: str = "openai/gpt-4o-mini"
    OPENROUTER_FALLBACK_MODELS: str | None = (
        None  # Comma-separated list of fallback models
    )
    OPENROUTER_SITE_URL: str | None = None
    OPENROUTER_APP_NAME: str | None = None

    # Chat agent
    CHAT_ENABLED: bool = True
    CHAT_MAX_TOOL_ROUNDS: int = 8
    CHAT_CONVERSATION_TTL_SECONDS: int = 60 * 60 * 24 * 7
    CHAT_MODEL: str | None = "openai/gpt-5.4-mini"
    TAVILY_API_KEY: str | None = None
    CHAT_WEB_SEARCH_MAX_RESULTS: int = 5
    # Chat observability: persist turns/llm-calls/tool-calls to Postgres.
    CHAT_OBSERVABILITY_ENABLED: bool = True
    # In-progress billing reservations older than this stop consuming quota.
    CHAT_TURN_RESERVATION_TTL_SECONDS: int = 900

    # Feedback
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_OWNER_ID: str

    # WhatsApp Integration Settings
    WHATSAPP_ENABLED: bool = False
    WHATSAPP_ACCESS_TOKEN: str
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    WHATSAPP_VERIFY_TOKEN: str | None = None
    WHATSAPP_API_VERSION: str = "v22.0"

    # WAHA
    WAHA_SESSION: str
    WAHA_URL: str
    WHATSAPP_API_KEY: str

    REDIS_URL: str
    REDIS_TOKEN: str

    # Axiom Logging Settings
    AXIOM_DATASET: str = "cleverbill"
    AXIOM_API_TOKEN: str | None = None
    AXIOM_ENABLED: bool = False
    AXIOM_SAMPLE_RATE: float = 0.05  # Sample 5% of successful requests
    AXIOM_SLOW_REQUEST_THRESHOLD_MS: int = 2000  # Consider requests >2s as slow

    DEPLOYMENT_ID: str | None = None  # For tracking deployments
    REGION: str | None = None  # e.g., "us-east-1", "eu-west-1"

    # ---------- Polar.sh subscriptions ----------
    POLAR_ACCESS_TOKEN: str | None = None
    POLAR_WEBHOOK_SECRET: str | None = None
    POLAR_ORGANIZATION_ID: str | None = None
    # "sandbox" targets api.sandbox.polar.sh; "production" targets api.polar.sh.
    POLAR_SERVER: str = "sandbox"
    # Public base URL of this backend, used to build Polar success_url for
    # checkout. Falls back to SERVER_HOST when not set.
    POLAR_SUCCESS_URL_BASE: AnyHttpUrl | None = None
    # Polar product ids per paying tier. Optional in sandbox; production
    # startup requires three non-empty, unique ids.
    POLAR_PRODUCT_TIER1_ID: str | None = None
    POLAR_PRODUCT_TIER2_ID: str | None = None
    POLAR_PRODUCT_TIER3_ID: str | None = None
    POLAR_WEBHOOK_MAX_BYTES: int = 262_144
    # Master switch. None/unset = auto-enable when POLAR_ACCESS_TOKEN is set.
    # Explicit false always disables (even if a token is present). Explicit true
    # (or auto-enable) requires access token + webhook secret + org id.
    SUBSCRIPTIONS_ENABLED: bool | None = None
    # Used only when SUBSCRIPTIONS_ENABLED is False. Sandbox/dev default is
    # tier3 for full chat locally; production always forces free (see validator).
    SUBSCRIPTIONS_DEV_DEFAULT_TIER: str = "tier3"

    @field_validator("POLAR_SERVER")
    def _polar_server_value(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("sandbox", "production"):
            raise ValueError("POLAR_SERVER must be 'sandbox' or 'production'")
        return v

    @field_validator("SUBSCRIPTIONS_DEV_DEFAULT_TIER")
    def _dev_default_tier_value(cls, v: str, info: dict[str, Any]) -> str:
        v = (v or "").strip().lower()
        if v not in ("free", "tier1", "tier2", "tier3"):
            v = "tier3"
        # Fail closed in production: never grant a paid tier when Polar is off.
        if info.data.get("POLAR_SERVER") == "production":
            return "free"
        return v

    @field_validator("SUBSCRIPTIONS_ENABLED", mode="before")
    def _subscriptions_enabled_requires_creds(
        cls, v: Any, info: dict[str, Any]
    ) -> bool:
        has_token = bool((info.data.get("POLAR_ACCESS_TOKEN") or "").strip())

        if v is None or (isinstance(v, str) and not v.strip()):
            enabled = has_token
        elif v is False or (
            isinstance(v, str) and v.strip().lower() in ("false", "0", "no", "off")
        ):
            enabled = False
        elif v is True or (
            isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "on")
        ):
            enabled = True
        else:
            enabled = has_token

        if enabled:
            missing = [
                name
                for name in (
                    "POLAR_ACCESS_TOKEN",
                    "POLAR_WEBHOOK_SECRET",
                    "POLAR_ORGANIZATION_ID",
                )
                if not (info.data.get(name) or "").strip()
            ]
            if missing:
                raise ValueError(
                    "SUBSCRIPTIONS_ENABLED=true requires: " + ", ".join(missing)
                )
        return enabled

    @model_validator(mode="after")
    def _validate_production_polar(self) -> "Settings":
        if not self.SUBSCRIPTIONS_ENABLED or self.POLAR_SERVER != "production":
            return self

        product_ids = [
            (self.POLAR_PRODUCT_TIER1_ID or "").strip(),
            (self.POLAR_PRODUCT_TIER2_ID or "").strip(),
            (self.POLAR_PRODUCT_TIER3_ID or "").strip(),
        ]
        if not all(product_ids):
            raise ValueError(
                "Production subscriptions require all POLAR_PRODUCT_TIER*_ID values"
            )
        if len(set(product_ids)) != len(product_ids):
            raise ValueError("Production Polar product ids must be unique")

        success_base = str(self.POLAR_SUCCESS_URL_BASE or self.SERVER_HOST)
        if not success_base.lower().startswith("https://"):
            raise ValueError("Production Polar checkout success URL must use HTTPS")
        return self

    @field_validator("WHATSAPP_ENABLED", mode="before")
    def get_whatsapp_enabled(cls, v: bool, info: dict[str, Any]) -> bool:
        return bool(
            info.data.get("WHATSAPP_ACCESS_TOKEN")
            and info.data.get("WHATSAPP_PHONE_NUMBER_ID")
            and bool(info.data.get("WHATSAPP_VERIFY_TOKEN"))
        )

    @field_validator("AXIOM_ENABLED", mode="before")
    def get_axiom_enabled(cls, v: bool, info: dict[str, Any]) -> bool:
        return bool(info.data.get("AXIOM_API_TOKEN"))

    class Config:
        case_sensitive = True


settings = Settings()
