"""Polar.sh SDK wrapper for customer, checkout, portal and webhook handling.

All Polar API calls go through this module so the rest of the app stays
unaware of the SDK specifics. When `SUBSCRIPTIONS_ENABLED` is False every
function short-circuits (the entitlements service returns the dev-default
tier instead).

Server selection: `POLAR_SERVER=sandbox` targets `https://sandbox-api.polar.sh`,
`production` targets `https://api.polar.sh`.

Async convention: the polar-sdk exposes async variants with an `_async` suffix
(e.g. `polar.customers.create_async`). We use those throughout.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tiers import Tier, get_tier_config
from app.crud import crud_subscription, crud_user
from app.models.user import User
from app.schemas.subscription import CheckoutOut, PortalOut
from app.utilities.wide_events import enrich_event, mark_for_logging

_polar_client: Any = None
_product_ids_configured: bool = False

# Polar webhook event types we handle.
SUBSCRIPTION_EVENTS = frozenset(
    {
        "subscription.created",
        "subscription.updated",
        "subscription.active",
        "subscription.canceled",
        "subscription.uncanceled",
        "subscription.revoked",
        "subscription.past_due",
    }
)
CUSTOMER_EVENTS = frozenset({"customer.created", "customer.updated"})


def _polar_log(request: Optional[Request], **fields: Any) -> None:
    """Attach Polar context to the request wide event (Axiom)."""
    if request is None:
        return
    enrich_event(request, polar=fields)
    if fields.get("level") in ("error", "warning"):
        mark_for_logging(request)


def _enabled() -> bool:
    return bool(settings.SUBSCRIPTIONS_ENABLED)


def get_polar_client() -> Any:
    """Lazily build and cache the Polar SDK client. Returns None if disabled."""
    global _polar_client
    if not _enabled():
        return None
    if _polar_client is None:
        from polar_sdk import Polar  # type: ignore[import-not-found]

        _polar_client = Polar(
            access_token=settings.POLAR_ACCESS_TOKEN,
            server=settings.POLAR_SERVER,
        )
    return _polar_client


def _configure_product_ids() -> None:
    """Push env-configured product ids into the tier catalog (once)."""
    global _product_ids_configured
    if _product_ids_configured:
        return
    from app.core.tiers import configure_product_ids

    configure_product_ids(
        {
            "tier1": settings.POLAR_PRODUCT_TIER1_ID,
            "tier2": settings.POLAR_PRODUCT_TIER2_ID,
            "tier3": settings.POLAR_PRODUCT_TIER3_ID,
        }
    )
    _product_ids_configured = True


def _external_id(user: User) -> str:
    return user.uuid or str(user.id)


def _success_url() -> str:
    base = (
        settings.POLAR_SUCCESS_URL_BASE
        or str(settings.SERVER_HOST).rstrip("/")
    )
    return f"{base}/account?checkout=success"


async def _resolve_user_by_external_id(
    db: AsyncSession, external_id: str
) -> Optional[User]:
    """external_id is `User.uuid` or `str(User.id)`."""
    # Try Clerk UUID first.
    user = await crud_user.get_by_uuid(db, uuid=external_id)
    if user is not None:
        return user
    # Fall back to numeric id.
    if external_id.isdigit():
        user = await crud_user.get(db, id=int(external_id))
        if user is not None:
            return user
    return None


async def ensure_customer(
    db: AsyncSession, user: User, request: Optional[Request] = None
) -> str:
    """Return the Polar customer id for `user`, creating the customer if needed.

    Caches the id on `User.polar_customer_id` and the subscription row.
    """
    if not _enabled():
        raise HTTPException(status_code=503, detail="Subscriptions are disabled")

    if user.polar_customer_id:
        return user.polar_customer_id

    polar = get_polar_client()
    ext_id = _external_id(user)
    # First try to look up by external id (the customer may already exist
    # from a prior checkout that didn't finish, or created in the dashboard).
    try:
        existing = await polar.customers.get_external_async(external_id=ext_id)
        customer_id = existing.id
    except Exception as exc:
        # ResourceNotFound -> create; anything else -> bubble up.
        if "ResourceNotFound" not in type(exc).__name__ and "404" not in str(exc):
            _polar_log(
                request,
                level="warning",
                op="customers.get_external",
                error=str(exc),
            )
        # Create a new customer tied to our external id.
        try:
            created = await polar.customers.create_async(
                request={
                    "external_id": ext_id,
                    "email": user.email or "",
                    "name": user.name or "",
                    "metadata": {"toston_user_id": str(user.id)},
                }
            )
            customer_id = created.id
        except Exception as create_exc:
            _polar_log(
                request,
                level="error",
                op="customers.create",
                error=str(create_exc),
            )
            raise HTTPException(status_code=502, detail="Failed to create Polar customer") from create_exc

    try:
        await crud_subscription.set_polar_customer_id(db, user.id, customer_id)
    except ValueError as exc:
        _polar_log(
            request,
            level="error",
            op="customers.bind",
            error=str(exc),
            polar_customer_id=customer_id,
        )
        raise HTTPException(
            status_code=409,
            detail="Polar customer is already linked to another account",
        ) from exc
    # Keep the in-memory user in sync for the rest of the request.
    user.polar_customer_id = customer_id
    return customer_id


async def create_checkout(
    db: AsyncSession,
    user: User,
    tier: Tier,
    request: Optional[Request] = None,
) -> CheckoutOut:
    """Create a Polar checkout session for the given tier's product."""
    if not _enabled():
        raise HTTPException(status_code=503, detail="Subscriptions are disabled")
    if tier == Tier.free:
        raise HTTPException(status_code=400, detail="Cannot subscribe to the free tier")

    _configure_product_ids()
    cfg = get_tier_config(tier)

    if not cfg.polar_product_id:
        raise HTTPException(
            status_code=409,
            detail=f"No Polar product configured for {tier.value}",
        )

    customer_id = await ensure_customer(db, user, request=request)
    polar = get_polar_client()
    try:
        checkout = await polar.checkouts.create_async(
            request={
                "products": [cfg.polar_product_id],
                "customer_id": customer_id,
                "success_url": _success_url(),
                "metadata": {"toston_user_id": str(user.id), "tier": tier.value},
            }
        )
    except Exception as exc:
        _polar_log(request, level="error", op="checkouts.create", error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to create checkout session") from exc

    return CheckoutOut(url=checkout.url, checkout_id=checkout.id)


async def create_portal_session(
    user: User, request: Optional[Request] = None
) -> PortalOut:
    """Create a Polar customer-portal session for self-service management."""
    if not _enabled():
        raise HTTPException(status_code=503, detail="Subscriptions are disabled")
    if not user.polar_customer_id:
        raise HTTPException(
            status_code=409,
            detail="No Polar customer found for this user",
        )
    polar = get_polar_client()
    try:
        session = await polar.customer_sessions.create_async(
            request={"external_customer_id": _external_id(user)}
        )
    except Exception as exc:
        _polar_log(
            request, level="error", op="customer_sessions.create", error=str(exc)
        )
        raise HTTPException(status_code=502, detail="Failed to create portal session") from exc

    return PortalOut(url=session.customer_portal_url)


# ==================== Webhook handling ====================


async def handle_polar_event(
    db: AsyncSession, event: Any, request: Optional[Request] = None
) -> None:
    """Dispatch a verified Polar webhook event to the right upsert path.

    `event` is the pydantic model returned by `polar_sdk.webhooks.validate_event`.
    Each payload has a `.type` string and a `.data` object.
    """
    event_type: str = (getattr(event, "TYPE", None) or getattr(event, "type", "") or "")
    data: Any = getattr(event, "data", None)

    _configure_product_ids()

    if event_type in SUBSCRIPTION_EVENTS:
        await _handle_subscription_event(db, event_type, data, request=request)
    elif event_type in CUSTOMER_EVENTS:
        await _handle_customer_event(db, data)
    else:
        # Acknowledge but ignore (checkout.*, order.*, benefit_grant.*, etc.)
        _polar_log(
            request, level="info", op="ignore_event", event_type=event_type
        )


def _coerce_dt(value: Any) -> Optional[Any]:
    if value is None:
        return None
    return value


def _event_at(data: Any, request: Optional[Request] = None) -> datetime:
    """Best-effort event time for out-of-order webhook protection."""
    for attr in ("modified_at", "updated_at", "created_at"):
        value = getattr(data, attr, None) if data is not None else None
        if value is None:
            continue
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if request is not None:
        ts = request.headers.get("webhook-timestamp")
        if ts:
            try:
                return datetime.fromtimestamp(float(ts), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass
    return datetime.now(timezone.utc)


async def _resolve_user_from_polar_customer(
    db: AsyncSession,
    polar_customer_id: str,
    request: Optional[Request] = None,
) -> Optional[User]:
    """Find our User from a Polar customer id.

    Resolution order:
    0. Fast path: `User.polar_customer_id` is denormalized at checkout (and
       by `customer.created`/`updated` webhooks), so a single indexed lookup
       resolves the user with no network calls.
    1. Look for a `UserSubscription` row with this polar_customer_id.
    2. Fall back to the Polar API to fetch the customer's external_id and
       resolve via `_resolve_user_by_external_id` (Clerk UUID or numeric id).
    """
    from sqlalchemy import select

    from app.models.subscription import UserSubscription
    from app.models.user import User as UserModel

    # 0. Fast path: User row already carries polar_customer_id.
    user = (
        await db.execute(
            select(UserModel).where(UserModel.polar_customer_id == polar_customer_id)
        )
    ).scalars().first()
    if user is not None:
        return user

    # 1. Subscription row lookup (kept for resilience / older rows).
    row = (
        await db.execute(
            select(UserSubscription).where(
                UserSubscription.polar_customer_id == polar_customer_id
            )
        )
    ).scalars().first()
    if row is not None:
        user = await crud_user.get(db, id=row.user_id)
        if user is not None:
            return user

    # 2. Fall back to Polar API to discover the external id.
    polar = get_polar_client()
    if polar is None:
        return None
    try:
        customer = await polar.customers.get_async(id=polar_customer_id)
    except Exception as exc:
        _polar_log(
            request,
            level="warning",
            op="customers.get",
            polar_customer_id=polar_customer_id,
            error=str(exc),
        )
        return None
    try:
        from polar_sdk.types import UNSET  # type: ignore[import-not-found]

        external_id = getattr(customer, "external_id", None)
        if external_id is UNSET or external_id is None:
            return None
    except ImportError:
        external_id = getattr(customer, "external_id", None)
        if not external_id:
            return None
    user = await _resolve_user_by_external_id(db, external_id)
    if user is not None:
        try:
            await crud_subscription.set_polar_customer_id(db, user.id, polar_customer_id)
            user.polar_customer_id = polar_customer_id
        except ValueError as exc:
            _polar_log(
                request,
                level="error",
                op="customers.bind",
                error=str(exc),
                polar_customer_id=polar_customer_id,
            )
            return None
    return user


async def _handle_subscription_event(
    db: AsyncSession,
    event_type: str,
    data: Any,
    request: Optional[Request] = None,
) -> None:
    if data is None:
        return
    polar_customer_id: Optional[str] = getattr(data, "customer_id", None)
    if not polar_customer_id:
        _polar_log(
            request,
            level="warning",
            op="subscription_event",
            event_type=event_type,
            reason="missing_customer_id",
        )
        return

    user = await _resolve_user_from_polar_customer(
        db, polar_customer_id, request=request
    )
    if user is None:
        _polar_log(
            request,
            level="warning",
            op="subscription_event",
            event_type=event_type,
            polar_customer_id=polar_customer_id,
            reason="no_local_user",
        )
        return

    product_id: Optional[str] = getattr(data, "product_id", None)
    # Map the event type to a status string we persist.
    status = _subscription_status_for_event(event_type, data)
    # Derive the tier we'll persist from the configured product id.
    from app.core.tiers import tier_for_product

    resolved_tier = tier_for_product(product_id)

    _polar_log(
        request,
        level="info",
        op="subscription_event",
        event_type=event_type,
        user_id=user.id,
        polar_customer_id=polar_customer_id,
        status=status,
        tier=resolved_tier.value,
    )

    row = await crud_subscription.upsert_from_webhook(
        db,
        user_id=user.id,
        polar_customer_id=polar_customer_id,
        polar_subscription_id=getattr(data, "id", None),
        product_id=product_id,
        status=status,
        current_period_start=_coerce_dt(getattr(data, "current_period_start", None)),
        current_period_end=_coerce_dt(getattr(data, "current_period_end", None)),
        canceled_at=_coerce_dt(getattr(data, "canceled_at", None)),
        metadata={"event_type": event_type},
        event_at=_event_at(data, request),
    )
    if row is None:
        _polar_log(
            request,
            level="info",
            op="subscription_event_skipped",
            event_type=event_type,
            user_id=user.id,
            reason="stale_event",
        )
        return
    # Drop the cached entitlement so the next chat turn reflects the change.
    from app.utilities.redis import invalidate_entitlement

    await invalidate_entitlement(user.id)


def _subscription_status_for_event(event_type: str, data: Any) -> str:
    # Prefer the explicit status on the subscription object when present.
    status_attr = getattr(data, "status", None)
    if status_attr is not None:
        # SubscriptionStatus is a str enum; use its value.
        return getattr(status_attr, "value", str(status_attr))
    # Derive from the event type as a fallback.
    mapping = {
        "subscription.active": "active",
        "subscription.created": "incomplete",
        "subscription.updated": "active",
        "subscription.past_due": "past_due",
        "subscription.canceled": "canceled",
        "subscription.revoked": "canceled",
        "subscription.uncanceled": "active",
    }
    return mapping.get(event_type, "active")


async def _handle_customer_event(db: AsyncSession, data: Any) -> None:
    if data is None:
        return
    polar_customer_id: Optional[str] = getattr(data, "id", None)
    external_id: Optional[str] = getattr(data, "external_id", None)
    if not polar_customer_id or not external_id:
        return
    user = await _resolve_user_by_external_id(db, external_id)
    if user is None:
        return
    try:
        await crud_subscription.set_polar_customer_id(db, user.id, polar_customer_id)
    except ValueError:
        # Already bound to another local user; refuse cross-account takeover.
        return
