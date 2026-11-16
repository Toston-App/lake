from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApiTokenScope = Literal["read", "write"]


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: ApiTokenScope = "write"
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiToken(BaseModel):
    """A token as listed: never its plaintext or its hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prefix: str
    scope: ApiTokenScope
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None


class ApiTokenCreated(ApiToken):
    """Returned once, by the create call: the only time ``token`` is shown."""

    token: str


class AiConnection(BaseModel):
    """A client that used the MCP endpoint for the user."""

    client: str | None = None
    kind: Literal["oauth", "token"]
    last_used_at: datetime
    calls_last_30_days: int
    token_id: int | None = None
    token_name: str | None = None
    token_active: bool | None = None


class AiConnections(BaseModel):
    """The MCP endpoint's address, the user's settings and the clients that used it."""

    enabled: bool
    mcp_url: str | None = None
    # AI apps may only read the user's data.
    read_only: bool = False
    calls_this_month: int = 0
    # None => unlimited
    monthly_call_limit: int | None = None
    connections: list[AiConnection]


class AiConnectionSettings(BaseModel):
    read_only: bool
