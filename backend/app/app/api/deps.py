from collections.abc import AsyncGenerator, Generator
from enum import Enum
import calendar
from datetime import date as Date, datetime, timedelta
from typing import Tuple, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyCookie, OAuth2PasswordBearer
from jose import jwt
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.core import security
from app.core.config import settings
from app.db.session import SessionLocal, async_session

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


def parse_date_filter(date_filter_type: DateFilterType, date: str) -> Tuple[Optional[Date], Optional[Date]]:
    """
    Parse date filter parameters and return start_date and end_date.
    """
    start_date: Date | None = None
    end_date: Date | None = None

    if date_filter_type == DateFilterType.date:
        try:
            start_date = datetime.strptime(date, "%Y-%m-%d").date()
            end_date = start_date
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

    elif date_filter_type == DateFilterType.week:
        try:
            start_date = datetime.strptime(date, "%Y-%m-%d").date()
            end_date = start_date + timedelta(days=7)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be a date in the format YYYY-MM-DD"
            )

    elif date_filter_type == DateFilterType.month:
        try:
            start_date = datetime.strptime(date, "%Y-%m").date()
            _, num_days = calendar.monthrange(start_date.year, start_date.month)
            end_date = start_date + timedelta(days=num_days - 1)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY-MM"
            )

    elif date_filter_type == DateFilterType.quarter:
        try:
            year_str, quarter_str = date.split("-")
            quarterNum = int(quarter_str.replace("Q", ""))
            year = int(year_str)

            if quarterNum < 1 or quarterNum > 4:
                raise ValueError("Quarter must be between 1 and 4")

            start_month = (quarterNum - 1) * 3 + 1
            end_month = quarterNum * 3
            start_date = Date(year, start_month, 1)
            _, end_day = calendar.monthrange(year, end_month)
            end_date = Date(year, end_month, end_day)

        except (ValueError, IndexError):
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY-QX"
            )

    elif date_filter_type == DateFilterType.year:
        try:
            year = int(date)
            start_date = Date(year, 1, 1)
            end_date = Date(year, 12, 31)
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=400, detail="Date must be in the format YYYY"
            )

    elif date_filter_type == DateFilterType.range:
        try:
            start_date_str, end_date_str = date.split(":")
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Date range must be in the format YYYY-MM-DD:YYYY-MM-DD",
            )

        if start_date > end_date:
            raise HTTPException(
                status_code=400, detail="Start date must be before end date"
            )

    return start_date, end_date


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
    # TODO add a env var to switch between the devel and prod and change this
    for key in [security.PUBLIC_KEY, "foo"]:
        try:
            if key == "foo":
                payload = jwt.decode(token, key, algorithms=["HS256"])
                has_email = payload.get("user").get("email")
            else:
                payload = jwt.decode(token, key, algorithms=[security.ALGORITHM])
                has_email = payload.get("email")

            if has_email:
                token_data = schemas.TokenPayload(**payload)
            else:
                token_data = schemas.TokenPayloadUuid(**payload)

            break  # If decoding succeeds, exit the loop
        except (jwt.JWTError, ValidationError) as e:
            print("🚀 ~ jwt.JWTError:", e)
            if key == "foo":  # If this was the last attempt
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Could not validate credentials",
                )

    if has_email:
        user = await crud.user.get(db, id=token_data.user["id"])
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
