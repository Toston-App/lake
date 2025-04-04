from datetime import datetime, timedelta, timezone
from typing import Any

import phonenumbers
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic.networks import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.core import security
from app.core.config import settings
from app.utilities.encryption import hash_sha256
from app.utils import send_new_account_email

router = APIRouter()


@router.get("", response_model=list[schemas.User])
async def read_users(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Retrieve users.
    """
    users = await crud.user.get_multi(db, skip=skip, limit=limit)
    return users


@router.post("", response_model=schemas.User)
async def create_user(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    user_in: schemas.UserCreate,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Create new user.
    """
    user = await crud.user.get_by_email(db, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this username already exists in the system.",
        )
    user = await crud.user.create(db, obj_in=user_in)
    if settings.EMAILS_ENABLED and user_in.email:
        send_new_account_email(
            email_to=user_in.email, username=user_in.email, password=user_in.password
        )
    return user


@router.put("/me", response_model=schemas.User)
async def update_user_me(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    password: str = Body(None),
    name: str = Body(None),
    email: EmailStr = Body(None),
    country: str = Body(None),
    phone: str = Body(None),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update own user.
    """
    current_user_data = jsonable_encoder(current_user)
    user_in = schemas.UserUpdate(**current_user_data)

    if password is not None:
        user_in.password = password
    if name is not None:
        user_in.name = name
    if email is not None:
        user_in.email = email
    if country is not None:
        user_in.country = country
    if phone is not None:
        # Check if phone is already registered to another user
        existing_user = await crud.user.get_by_phone(db, phone=phone)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(
                status_code=400,
                detail="Phone number already registered to another user",
            )

        try:
            phone_num = phonenumbers.parse(phone, None)
            is_valid = phonenumbers.is_valid_number(phone_num)

            if(is_valid == False):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid phone number",
                )

            formatted_phone  = phonenumbers.format_number(phone_num, phonenumbers.PhoneNumberFormat.E164)

            # Ensure Mexican mobile numbers start with +521 (add '1' if missing), this to match whatsapp phone format
            if phone_num.country_code == 52 and not formatted_phone.startswith("+521"):
                formatted_phone = "+521" + formatted_phone[3:]

            user_in.phone = hash_sha256(formatted_phone)
        except phonenumbers.phonenumberutil.NumberParseException:
            raise HTTPException(
                status_code=400,
                detail="Invalid phone number",
            )

    user_in.updated_at = datetime.now(timezone.utc)
    user = await crud.user.update(db, db_obj=current_user, obj_in=user_in)
    return user


@router.get("/me", response_model=schemas.User)
async def read_user_me(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get current user.
    """
    return current_user


@router.post("/open", response_model=schemas.Msg)
async def create_user_open(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    uuid: str = Body(...),
    use_email: bool = Body(False),
    email: EmailStr = Body(None),
    password: str = Body(None),
    name: str = Body(None),
    country: str = Body(None),
) -> Any:
    """
    Create new user without the need to be logged in.
    """
    if not settings.USERS_OPEN_REGISTRATION:
        raise HTTPException(
            status_code=403,
            detail="Open user registration is forbidden on this server",
        )

    if use_email:
        if not email or not password or not name or not country:
            raise HTTPException(
                status_code=400,
                detail="Email, password, name and country are required",
            )

        user = await crud.user.get_by_email(db, email=email)
        if user:
            raise HTTPException(
                status_code=400,
                detail="The user with this email already exists in the system",
            )

        user_in = schemas.UserCreate(
            password=password, email=email, name=name, country=country
        )
        user = await crud.user.create(db, obj_in=user_in)

        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = security.create_access_token(
            jsonable_encoder(user), expires_delta=access_token_expires
        )

        response = JSONResponse(
            content={"msg": "User created successfully", "jwt": access_token}
        )
        response.set_cookie(
            key="__session",
            value=access_token,
            httponly=True,
            secure=True,
            samesite="Lax",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
        return response

    # UUID auth
    user = await crud.user.get_by_uuid(db, uuid=uuid)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this username already exists in the system",
        )
    user_in = schemas.UserCreateUuid(uuid=uuid)
    user = await crud.user.create(db, obj_in=user_in)

    return {"msg": "User created successfully"}


@router.get("/{user_id}", response_model=schemas.User)
async def read_user_by_id(
    user_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.async_get_db),
) -> Any:
    """
    Get a specific user by id.
    """
    if not crud.user.is_superuser(current_user):
        raise HTTPException(
            status_code=400, detail="The user doesn't have enough privileges"
        )

    user = await crud.user.get(db, id=user_id)

    return user


@router.put("/{user_id}", response_model=schemas.User)
async def update_user(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    user_id: int,
    user_in: schemas.UserUpdate,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Update a user.
    """
    user = await crud.user.get(db, id=user_id)

    if user.id != current_user.id and not crud.user.is_superuser(current_user):
        raise HTTPException(
            status_code=400,
            detail="The user doesn't have enough privileges",
        )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this username does not exist in the system",
        )

    user = await crud.user.update(db, db_obj=user, obj_in=user_in)
    return user


@router.delete("/{id}", response_model=schemas.User)
async def delete_user(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Delete a user.
    """
    user = await crud.user.get(db, id=id)

    if user.id != current_user.id and not crud.user.is_superuser(current_user):
        raise HTTPException(
            status_code=400,
            detail="The user doesn't have enough privileges",
        )

    if user == current_user:
        raise HTTPException(
            status_code=400,
            detail="You cannot delete your own account",
        )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this username does not exist in the system",
        )

    user = await crud.user.remove(db=db, id=id)
    return user
