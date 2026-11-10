"""Small Clerk Backend API wrapper: profile lookups and OAuth token checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class ClerkProfile:
    email: str | None
    name: str | None


class ClerkProfileError(RuntimeError):
    """Raised when a configured Clerk profile lookup fails."""


def _profile_from_user(clerk_user: Any) -> ClerkProfile:
    email_addresses = getattr(clerk_user, "email_addresses", None) or []
    primary_email_id = getattr(clerk_user, "primary_email_address_id", None)

    primary = next(
        (
            address
            for address in email_addresses
            if getattr(address, "id", None) == primary_email_id
        ),
        None,
    )
    if primary is None and email_addresses:
        primary = email_addresses[0]

    email = getattr(primary, "email_address", None) if primary is not None else None
    email = email.strip() if isinstance(email, str) and email.strip() else None

    name_parts = (
        getattr(clerk_user, "first_name", None),
        getattr(clerk_user, "last_name", None),
    )
    name_value = " ".join(
        part.strip() for part in name_parts if part and part.strip()
    )
    name: str | None = name_value or None
    if name is None:
        username = getattr(clerk_user, "username", None)
        name = username.strip() if isinstance(username, str) and username.strip() else None

    return ClerkProfile(email=email, name=name)


async def get_clerk_profile(user_id: str) -> ClerkProfile:
    """Fetch a Clerk user's primary email and displayable name."""
    secret_key = (settings.CLERK_SECRET_KEY or "").strip()
    if not secret_key:
        raise ClerkProfileError("CLERK_SECRET_KEY is not configured")

    from clerk_backend_api import Clerk

    try:
        async with Clerk(bearer_auth=secret_key) as clerk:
            clerk_user = await clerk.users.get_async(user_id=user_id)
    except Exception as exc:
        raise ClerkProfileError("Failed to retrieve Clerk user") from exc

    return _profile_from_user(clerk_user)


CLERK_API_URL = "https://api.clerk.com/v1"


class ClerkUnavailable(RuntimeError):
    """Clerk could not answer (network error, rate limit or server error)."""


async def verify_oauth_access_token(token: str) -> dict[str, Any] | None:
    """Look up an opaque Clerk OAuth access token with the Backend API.

    Returns Clerk's token record, or None when Clerk does not know the token or
    no secret key is configured. Raises ClerkUnavailable when Clerk cannot
    answer, so callers can tell "invalid" apart from "try again later". The
    record is returned unchecked: audience, expiry and revocation are the
    caller's policy.
    """
    secret_key = (settings.CLERK_SECRET_KEY or "").strip()
    if not secret_key:
        return None
    try:
        async with httpx.AsyncClient(base_url=CLERK_API_URL, timeout=5) as client:
            response = await client.post(
                "/oauth_applications/access_tokens/verify",
                json={"access_token": token},
                headers={"Authorization": f"Bearer {secret_key}"},
            )
    except httpx.HTTPError as exc:
        raise ClerkUnavailable("Clerk token verification unreachable") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise ClerkUnavailable(f"Clerk token verification {response.status_code}")
    if response.status_code != 200:
        return None
    return response.json()
