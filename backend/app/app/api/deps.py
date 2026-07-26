import logging
from collections.abc import AsyncGenerator, Generator
from enum import Enum

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


def require_investments_access(
    current_user: models.User = Depends(get_current_active_user),
    request: Request = None,
) -> models.User:
    if request is not None:
        from app.utilities.investment_telemetry import (
            complete_investment_stage,
            fail_investment_event,
            start_investment_event,
        )

        start_investment_event(request, user_id=current_user.id)

    allowed_by_id = current_user.id in settings.investments_allowed_user_ids
    allowed_by_uuid = (
        current_user.uuid is not None
        and current_user.uuid in settings.investments_allowed_user_uuids
    )
    if not settings.INVESTMENTS_ENABLED or not (
        crud.user.is_superuser(current_user) or allowed_by_id or allowed_by_uuid
    ):
        if request is not None:
            fail_investment_event(
                request,
                reason="feature_access_denied",
                stage="access",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Investments feature access is not enabled for this user",
        )
    if request is not None:
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
