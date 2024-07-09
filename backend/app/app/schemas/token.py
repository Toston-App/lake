from typing import Optional

from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenPayload(BaseModel):
    user: dict = None

    # user:
    # name: str
    # email: str
    # country: str
    # id: int
