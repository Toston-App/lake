from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.core import security
from app.core.config import settings
from app.core.security import get_password_hash
from app.utils import (
    generate_password_reset_token,
    send_reset_password_email,
    verify_password_reset_token,
)
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
            jsonable_encoder(user), expires_delta=access_token_expires
        ),
        "token_type": "bearer",
    }


@router.post("/login/test-token", response_model=schemas.User)
def test_token(
    request: Request,
    current_user: models.User = Depends(deps.get_current_user),
) -> Any:
    """
    Test access token
    """
    enrich_event(
        request,
        auth={"type": "test_token"},
        user={"id": current_user.id, "email": current_user.email},
    )
    return current_user


@router.post("/password-recovery/{email}", response_model=schemas.Msg)
async def recover_password(
    request: Request,
    email: str,
    db: AsyncSession = Depends(deps.async_get_db),
) -> Any:
    """
    Password Recovery
    """
    mark_for_logging(request)
    enrich_event(
        request,
        auth={"type": "password_recovery", "email": email},
    )

    user = await crud.user.get_by_email(db, email=email)

    if not user:
        enrich_event(request, auth={"outcome": "failure", "reason": "user_not_found"})
        raise HTTPException(
            status_code=404,
            detail="The user with this username does not exist in the system.",
        )

    password_reset_token = generate_password_reset_token(email=email)
    send_reset_password_email(
        email_to=user.email, email=email, token=password_reset_token
    )

    enrich_event(
        request,
        auth={"outcome": "success"},
        user={"id": user.id},
    )
    return {"msg": "Password recovery email sent"}


@router.post("/reset-password", response_model=schemas.Msg)
async def reset_password(
    request: Request,
    token: str = Body(...),
    new_password: str = Body(...),
    db: AsyncSession = Depends(deps.async_get_db),
) -> Any:
    """
    Reset password
    """
    mark_for_logging(request)
    enrich_event(request, auth={"type": "password_reset"})

    email = verify_password_reset_token(token)
    if not email:
        enrich_event(request, auth={"outcome": "failure", "reason": "invalid_token"})
        raise HTTPException(status_code=400, detail="Invalid token")

    user = await crud.user.get_by_email(db, email=email)
    if not user:
        enrich_event(request, auth={"outcome": "failure", "reason": "user_not_found"})
        raise HTTPException(
            status_code=404,
            detail="The user with this username does not exist in the system.",
        )
    elif not crud.user.is_active(user):
        enrich_event(request, auth={"outcome": "failure", "reason": "inactive_user"})
        raise HTTPException(status_code=400, detail="Inactive user")

    hashed_password = get_password_hash(new_password)
    user.hashed_password = hashed_password
    db.add(user)
    await db.commit()

    enrich_event(
        request,
        auth={"outcome": "success"},
        user={"id": user.id, "email": user.email},
    )
    return {"msg": "Password updated successfully"}
