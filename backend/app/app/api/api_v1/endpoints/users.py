from datetime import datetime, timedelta, timezone
from typing import Any

import phonenumbers
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic.networks import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.schemas.account import Account as AccountSchema
from app.api import deps
from app.core import security
from app.core.config import settings
from app.utilities.encryption import hash_sha256
from app.utils import send_new_account_email
from app.utilities.wide_events import enrich_event, mark_for_logging, timed

router = APIRouter()


@router.get("", response_model=list[schemas.User])
async def read_users(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Retrieve users.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email, "is_superuser": True},
        query={"type": "list_users", "skip": skip, "limit": limit},
    )

    with timed() as t:
        users = await crud.user.get_multi(db, skip=skip, limit=limit)

    enrich_event(
        request,
        database={"operation": "list_users", "duration_ms": t.ms, "results_count": len(users)},
    )
    return users


@router.put("/me", response_model=bool)
async def update_user_me(
    *,
    request: Request,
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
    fields_updating = [
        f for f, v in [
            ("password", password), ("name", name), ("email", email),
            ("country", country), ("phone", phone),
        ] if v is not None
    ]
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "update_self",
            "fields_updating": fields_updating,
            "has_password_change": password is not None,
            "has_phone_change": phone is not None,
        },
    )

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
        try:
            phone_num = phonenumbers.parse(phone, None)
            is_valid = phonenumbers.is_valid_number(phone_num)

            if is_valid is False:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid phone number",
                )

            formatted_phone = phonenumbers.format_number(
                phone_num, phonenumbers.PhoneNumberFormat.E164
            )

            # Ensure Mexican mobile numbers start with +521 (add '1' if missing), this to match whatsapp phone format
            if phone_num.country_code == 52 and not formatted_phone.startswith("+521"):
                formatted_phone = "+521" + formatted_phone[3:]

            # Ensure Arg mobile numbers start with +549 (add '9' if missing), this to match whatsapp phone format
            if phone_num.country_code == 54 and not formatted_phone.startswith("+549"):
                formatted_phone = "+549" + formatted_phone[3:]

            phone_hash = hash_sha256(formatted_phone)

            # Check if this phone hash already exists for another user
            existing_user = await crud.user.get_by_phone(db, phone=phone_hash)
            if existing_user and existing_user.id != current_user.id:
                raise HTTPException(
                    status_code=400,
                    detail="Este número de teléfono ya está registrado por otro usuario. Si crees que esto es un error, por favor contacta al soporte.",
                )

            user_in.phone = phone_hash
        except phonenumbers.phonenumberutil.NumberParseException:
            raise HTTPException(
                status_code=400,
                detail="Invalid phone number",
            )

    user_in.updated_at = datetime.now(timezone.utc)
    await crud.user.update(db, db_obj=current_user, obj_in=user_in)
    return True

# we don't need this endpoint for now
# @router.get("/me", response_model=schemas.User)
# async def read_user_me(
#     db: AsyncSession = Depends(deps.async_get_db),
#     current_user: models.User = Depends(deps.get_current_active_user),
# ) -> Any:
#     """
#     Get current user.
#     """
#     return current_user


@router.post("/open", response_model=schemas.Msg)
async def create_user_open(
    *,
    request: Request,
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
    mark_for_logging(request)
    enrich_event(
        request,
        operation={
            "type": "user_registration",
            "method": "email" if use_email else "uuid",
        },
    )

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
            enrich_event(request, auth={"outcome": "failure", "reason": "email_exists"})
            raise HTTPException(
                status_code=400,
                detail="The user with this email already exists in the system",
            )

        user_in = schemas.UserCreate(
            password=password, email=email, name=name, country=country
        )
        user = await crud.user.create(db, obj_in=user_in)

        enrich_event(
            request,
            auth={"outcome": "success"},
            user={"id": user.id, "email": user.email, "country": country},
        )

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
        enrich_event(request, auth={"outcome": "failure", "reason": "uuid_exists"})
        raise HTTPException(
            status_code=400,
            detail="The user with this username already exists in the system",
        )
    user_in = schemas.UserCreateUuid(uuid=uuid)
    user = await crud.user.create(db, obj_in=user_in)

    enrich_event(
        request,
        auth={"outcome": "success"},
        user={"id": user.id},
    )

    return {"msg": "User created successfully"}


@router.get("/{user_id}", response_model=schemas.User)
async def read_user_by_id(
    request: Request,
    user_id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.async_get_db),
) -> Any:
    """
    Get a specific user by id.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email, "is_superuser": current_user.is_superuser},
        query={"type": "get_user_by_id", "target_user_id": user_id},
    )

    if not crud.user.is_superuser(current_user):
        raise HTTPException(
            status_code=400, detail="The user doesn't have enough privileges"
        )

    user = await crud.user.get(db, id=user_id)

    return user


@router.put("/{user_id}", response_model=schemas.User)
async def update_user(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    user_id: int,
    user_in: schemas.UserUpdate,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Update a user.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email, "is_superuser": True},
        operation={"type": "admin_update_user", "target_user_id": user_id},
    )

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
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_superuser),
) -> Any:
    """
    Delete a user.
    """
    mark_for_logging(request)
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email, "is_superuser": True},
        operation={"type": "delete_user", "target_user_id": id},
    )

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


@router.put("/me/default-account", response_model=AccountSchema)
async def set_default_account(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    account_id: int = Body(...),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Set the default account for WhatsApp transactions.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "set_default_account",
            "account_id": account_id,
            "previous_default": current_user.default_account_id,
        },
    )

    try:
        await crud.user.set_default_account(db, user_id=current_user.id, account_id=account_id)
        account = await crud.account.get_by_id(db, owner_id=current_user.id, id=account_id)
        return account
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/me/default-account", response_model=AccountSchema)
async def get_default_account(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get the default account for WhatsApp transactions.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        query={"type": "get_default_account"},
    )

    account = await crud.user.get_default_account(db, user_id=current_user.id)
    if not account:
        raise HTTPException(status_code=404, detail="No default account set")
    return account


@router.delete("/me/default-account", response_model=bool)
async def clear_default_account(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Clear the default account for WhatsApp transactions.
    """
    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={
            "type": "clear_default_account",
            "previous_default": current_user.default_account_id,
        },
    )

    try:
        await crud.user.clear_default_account(db, user_id=current_user.id)
        return True
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
