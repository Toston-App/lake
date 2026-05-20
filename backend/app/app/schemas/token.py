from typing import Literal

from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    token_type: str


class LocalTokenPayload(BaseModel):
    """Payload for tokens we issue ourselves via /login/access-token.

    `sub` is the database user id as a string.
    """

    sub: str
    iss: Literal["local"]
    iat: int
    exp: int
    jti: str


class ClerkTokenPayload(BaseModel):
    """Payload for tokens issued by Clerk.

    `sub` is the Clerk user id (matches our `User.uuid`).
    `azp` is the authorized party (frontend origin) and is checked against
    `settings.CLERK_AUTHORIZED_PARTIES` in `get_current_user`.

    See https://clerk.com/docs/backend-requests/resources/session-tokens
    """

    sub: str
    iss: str
    azp: str
    exp: int
    iat: int
    nbf: int
    sid: str
