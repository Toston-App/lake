from typing import Generator, AsyncGenerator
from enum import Enum

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, APIKeyCookie
from jose import jwt
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.core import security
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.session import async_session

# Use this to get the jwt like "Bearer" in the Authorization header
reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)

# Use cookie `__session` to get the jwt token
cookie_scheme = APIKeyCookie(
    name="__session",
    description="JWT token from Clerk",
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


async def get_current_user(
        db: AsyncSession = Depends(async_get_db), token: str = Depends(reusable_oauth2)
) -> models.User:
    print("🚀 ~ using reusable_oauth2")

    for key in [security.PUBLIC_KEY, "foo"]:
        try:
            if key == "foo":
                payload = jwt.decode(
                    token, key, algorithms=["HS256"]
                )
                has_email = payload.get('user').get('email')
            else:
                payload = jwt.decode(
                    token, key, algorithms=[security.ALGORITHM]
                )
                has_email = payload.get('email')

            if has_email:
                token_data = schemas.TokenPayload(**payload)
            else:
                token_data = schemas.TokenPayloadUuid(**payload)

            break  # If decoding succeeds, exit the loop
        except (jwt.JWTError, ValidationError):
            if key == "foo":  # If this was the last attempt
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Could not validate credentials",
                )

    if has_email:
        user = await crud.user.get(db, id=token_data.user['id'])
    else:
        user = await crud.user.get_by_uuid(db, uuid=token_data.sub)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def get_current_active_user(
        current_user: models.User = Depends(get_current_user),
) -> models.User:
    if not crud.user.is_active(current_user):
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def get_current_active_superuser(
        current_user: models.User = Depends(get_current_user),
) -> models.User:
    if not crud.user.is_superuser(current_user):
        raise HTTPException(
            status_code=400, detail="The user doesn't have enough privileges"
        )
    return current_user
