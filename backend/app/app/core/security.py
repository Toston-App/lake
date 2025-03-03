import base64
from datetime import datetime, timedelta
from typing import Any, Union

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


ALGORITHM = "RS256"  # Original algorithm was `HS256`
PUBLIC_KEY = base64.b64decode(settings.SECRET_KEY).decode("utf-8")
# TODO: I don't have the private key because I'm using Clerk auth and it olny provides the public key
PRIVATE_KEY = "secret"


def create_access_token(
    subject: Union[str, Any], expires_delta: timedelta = None
) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode = {
        "exp": expire,
        "user": {
            "name": subject["name"],
            "email": subject["email"],
            "country": subject["country"],
            "id": subject["id"],
        },
    }

    return jwt.encode(to_encode, "foo", algorithm="HS256")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
