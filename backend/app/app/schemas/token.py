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


class TokenPayloadUuid(BaseModel):
    azp: str
    exp: int
    iat: int
    iss: str
    nbf: int
    sid: str
    sub: str

    # https://clerk.com/docs/backend-requests/resources/session-tokens#default-session-claims
