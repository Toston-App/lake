from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from app.api import deps
from app.core import security
from app.core.config import settings
from app.utilities.wide_events import enrich_event, mark_for_logging

router = APIRouter()


@router.post("/login/access-token", response_model=schemas.Token)
async def login_access_token(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> Any:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    enrich_event(
        request,
        auth={"type": "login", "method": "password", "email": form_data.username},
    )

    user = await crud.user.authenticate(
        db, email=form_data.username, password=form_data.password
    )
    if not user:
        mark_for_logging(request)
        enrich_event(request, auth={"outcome": "failure", "reason": "invalid_credentials"})
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    elif not crud.user.is_active(user):
        mark_for_logging(request)
        enrich_event(request, auth={"outcome": "failure", "reason": "inactive_user"})
        raise HTTPException(status_code=400, detail="Inactive user")

    enrich_event(
        request,
        auth={"outcome": "success"},
        user={"id": user.id, "email": user.email, "is_superuser": user.is_superuser},
    )

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return {
        "access_token": security.create_access_token(
            user.id, expires_delta=access_token_expires
        ),
        "token_type": "bearer",
    }


# @router.post("/login/test-token", response_model=schemas.User)
# def test_token(
#     request: Request,
#     current_user: models.User = Depends(deps.get_current_user),
# ) -> Any:
#     """
#     Test access token
#     """
#     enrich_event(
#         request,
#         auth={"type": "test_token"},
#         user={"id": current_user.id, "email": current_user.email},
#     )
#     return current_user
