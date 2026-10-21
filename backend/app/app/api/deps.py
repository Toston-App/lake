import logging
from collections.abc import AsyncGenerator, Generator
from enum import Enum
from typing import NoReturn

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie, OAuth2PasswordBearer
from jose import jwt
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.core import security
from app.core.config import settings
from app.db.session import SessionLocal, async_session

logger = logging.getLogger(__name__)


# Bearer token from `Authorization: Bearer <jwt>`. auto_error=False so we can
# also accept the Clerk session cookie below; one of the two must be present.
reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token",
    auto_error=False,
)

# Clerk drops its session JWT in the `__session` cookie. We accept it as an
# alternative to the bearer header.
cookie_scheme = APIKeyCookie(
    name="__session",
    description="Session cookie (used by Clerk).",
    auto_error=False,
)


class DateFilterType(str, Enum):
    date = "date"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"
    range = "range"


def get_db() -> Generator:
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


async def async_get_db() -> AsyncGenerator:
    async with async_session() as session:
        yield session


_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def _verify_local_token(token: str) -> schemas.LocalTokenPayload:
    """Verify a token we issued ourselves (email/password login)."""
    payload = jwt.decode(
        token,
        settings.LOCAL_JWT_SECRET,
        algorithms=[security.LOCAL_ALGORITHM],
        issuer=security.LOCAL_ISSUER,
    )
    return schemas.LocalTokenPayload(**payload)


def verify_clerk_token(token: str) -> schemas.ClerkTokenPayload:
    """Verify a Clerk-issued session JWT.

    `jwt.decode` checks signature + `exp` + `nbf` + `iss`. We additionally
    enforce that `azp` is in our allow-list so a token minted for some
    other application can't be used here.
    """
    payload = jwt.decode(
        token,
        security._clerk_public_key(),
        algorithms=[security.CLERK_ALGORITHM],
        issuer=settings.CLERK_ISSUER,
    )
    azp = payload.get("azp")
    if not azp or azp not in settings.CLERK_AUTHORIZED_PARTIES:
        raise jwt.JWTError("Unauthorized party (azp)")
    return schemas.ClerkTokenPayload(**payload)


async def get_current_user(
    db: AsyncSession = Depends(async_get_db),
    bearer_token: str | None = Depends(reusable_oauth2),
    cookie_token: str | None = Depends(cookie_scheme),
) -> models.User:
    token = bearer_token or cookie_token
    if not token:
        raise _CREDENTIALS_EXCEPTION

    # Peek at the unverified `iss` claim to route to the correct verifier.
    # The routing itself is not a security boundary -- each branch fully
    # re-verifies the token with the appropriate key/algorithm.
    try:
        unverified = jwt.get_unverified_claims(token)
    except jwt.JWTError:
        raise _CREDENTIALS_EXCEPTION

    issuer = unverified.get("iss")

    try:
        if issuer == security.LOCAL_ISSUER:
            local_payload = _verify_local_token(token)
            user = await crud.user.get(db, id=int(local_payload.sub))
        elif issuer and issuer == settings.CLERK_ISSUER:
            clerk_payload = verify_clerk_token(token)
            user = await crud.user.get_by_uuid(db, uuid=clerk_payload.sub)
        else:
            raise _CREDENTIALS_EXCEPTION
    except (jwt.JWTError, ValidationError, ValueError) as e:
        logger.warning("JWT validation failed: %s", e)
        raise _CREDENTIALS_EXCEPTION from None

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def get_clerk_session_sub(
    bearer_token: str | None = Depends(reusable_oauth2),
    cookie_token: str | None = Depends(cookie_scheme),
) -> str:
    """Require a valid Clerk session and return the verified `sub` (user uuid).

    Used by endpoints that bootstrap a local row for a Clerk-authenticated
    user (e.g. `POST /users/open` UUID branch). The caller MUST NOT trust
    any UUID supplied in the request body -- only the value returned here.
    """
    token = bearer_token or cookie_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clerk session required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = verify_clerk_token(token)
    except (jwt.JWTError, ValidationError) as e:
        logger.warning("Clerk session validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Clerk session",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return payload.sub


def get_current_active_user(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    if not crud.user.is_active(current_user):
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def require_chat_access(
    current_user: models.User = Depends(get_current_active_user),
) -> models.User:
    """Require global chat enablement; every active user can use chat."""
    if not settings.CHAT_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat is disabled",
        )
    return current_user


# Methods that only read investment data; everything else creates, edits or
# deletes it.
_INVESTMENT_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Writes a lapsed subscriber may still make; the endpoint throttles them via
# request.state.investments_read_only.
_INVESTMENT_LAPSED_WRITES = frozenset(
    {("POST", f"{settings.API_V1_STR}/investments/assets/refresh-prices")}
)


async def require_investments_access(
    request: Request,
    current_user: models.User = Depends(get_current_active_user),
    db: AsyncSession = Depends(async_get_db),
) -> models.User:
    """Investments are a paid feature: any paid tier, or a superuser.

    A user whose subscription lapsed (canceled, revoked, payment failure) keeps
    read-only access so they can still see their portfolio, but can't add,
    edit or delete anything until they renew. They may still refresh prices,
    which the endpoint limits to once a day.
    """
    from app.core.tiers import FEATURE_INVESTMENTS, has_feature
    from app.crud import crud_subscription
    from app.services.entitlements import get_user_tier
    from app.utilities.investment_telemetry import (
        complete_investment_stage,
        fail_investment_event,
        start_investment_event,
    )

    start_investment_event(request, user_id=current_user.id)

    def deny(status_code: int, reason: str, detail: str) -> NoReturn:
        fail_investment_event(request, reason=reason, stage="access")
        raise HTTPException(status_code=status_code, detail=detail)

    if not settings.INVESTMENTS_ENABLED:
        deny(
            status.HTTP_403_FORBIDDEN,
            "feature_access_denied",
            "Investments feature is not enabled",
        )
    if not crud.user.is_superuser(current_user):
        tier, _ = await get_user_tier(db, current_user.id)
        if not has_feature(tier, FEATURE_INVESTMENTS):
            subscription = await crud_subscription.get_by_user(db, current_user.id)
            if subscription is None or not subscription.polar_subscription_id:
                deny(
                    status.HTTP_402_PAYMENT_REQUIRED,
                    "subscription_required",
                    "Investments require an active paid subscription",
                )
            request.state.investments_read_only = True
            if request.method not in _INVESTMENT_READ_METHODS and (
                (request.method, request.url.path) not in _INVESTMENT_LAPSED_WRITES
            ):
                deny(
                    status.HTTP_402_PAYMENT_REQUIRED,
                    "subscription_inactive",
                    "Your subscription is inactive: investments are read-only "
                    "until you renew",
                )
    complete_investment_stage(request, "access")
    return current_user


def get_current_active_superuser(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    if not crud.user.is_superuser(current_user):
        raise HTTPException(
            status_code=400, detail="The user doesn't have enough privileges"
        )
    return current_user
