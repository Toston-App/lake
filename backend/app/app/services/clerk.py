"""Small Clerk Backend API wrapper for billing profile lookups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

    print(f"Clerk user: {clerk_user}")
    return _profile_from_user(clerk_user)
