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
from typing import Any, cast

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tiers import Tier, get_tier_config
from app.crud import crud_subscription
from app.crud.crud_user import user as crud_user
from app.models.user import User
from app.schemas.subscription import ChangePlanOut, CheckoutOut, PortalOut
from app.services.clerk import ClerkProfileError, get_clerk_profile
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
_EVENT_PRIORITIES = {
    "subscription.created": 10,
    "subscription.updated": 20,
    "subscription.active": 30,
    "subscription.uncanceled": 40,
    "subscription.past_due": 50,
    "subscription.canceled": 60,
    "subscription.revoked": 70,
}
CUSTOMER_EVENTS = frozenset({"customer.created", "customer.updated"})


def _polar_log(request: Request | None, **fields: Any) -> None:
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
        from polar_sdk import Polar

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
        }
    )
    _product_ids_configured = True


def _external_id(user: User) -> str:
    return user.uuid or str(user.id)


def _success_url() -> str:
    base = str(settings.POLAR_SUCCESS_URL_BASE or settings.SERVER_HOST).rstrip("/")
    return f"{base}/account?checkout=success"


async def _resolve_user_by_external_id(
    db: AsyncSession, external_id: str
) -> User | None:
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
    db: AsyncSession, user: User, request: Request | None = None
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
    from polar_sdk.models import ResourceNotFound

    try:
        existing = await polar.customers.get_external_async(external_id=ext_id)
        customer_id = existing.id
    except ResourceNotFound:
        # Create a new customer tied to our external id.
        email = user.email.strip() if user.email else None
        name = user.name.strip() if user.name else None
        if not email and user.uuid:
            try:
                clerk_profile = await get_clerk_profile(user.uuid)
            except ClerkProfileError as exc:
                _polar_log(
                    request,
                    level="error",
                    op="clerk.users.get",
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=502,
                    detail="Failed to retrieve Clerk profile for Polar customer",
                ) from exc
            email = clerk_profile.email
            name = name or clerk_profile.name

        if not email:
            _polar_log(
                request,
                level="warning",
                op="customers.create",
                error="missing_email",
            )
            raise HTTPException(
                status_code=422,
                detail="An email address is required to create a Polar customer",
            )

        customer_request: dict[str, Any] = {
            "external_id": ext_id,
            "email": email,
            "metadata": {"toston_user_id": str(user.id)},
        }
        if name:
            customer_request["name"] = name

        try:
            created = await polar.customers.create_async(request=customer_request)
            customer_id = created.id
        except Exception as create_exc:
            _polar_log(
                request,
                level="error",
                op="customers.create",
                error=str(create_exc),
            )
            raise HTTPException(
                status_code=502, detail="Failed to create Polar customer"
            ) from create_exc
    except Exception as exc:
        _polar_log(
            request,
            level="error",
            op="customers.get_external",
            error=type(exc).__name__,
        )
        raise HTTPException(
            status_code=502, detail="Failed to look up Polar customer"
        ) from exc

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
    await db.commit()
    # Keep the in-memory user in sync for the rest of the request.
    user.polar_customer_id = customer_id
    return cast(str, customer_id)


async def create_checkout(
    db: AsyncSession,
    user: User,
    tier: Tier,
    request: Request | None = None,
) -> CheckoutOut:
    """Create a Polar checkout session for the given tier's product."""
    if not _enabled():
        raise HTTPException(status_code=503, detail="Subscriptions are disabled")
    if tier == Tier.free:
        raise HTTPException(status_code=400, detail="Cannot subscribe to the free tier")

    existing_subscription = await crud_subscription.get_by_user(db, user.id)
    if (
        existing_subscription is not None
        and existing_subscription.polar_subscription_id
        and existing_subscription.status in {"active", "trialing", "past_due"}
    ):
        raise HTTPException(
            status_code=409,
            detail="Manage the existing subscription instead of starting a new checkout",
        )

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
        raise HTTPException(
            status_code=502, detail="Failed to create checkout session"
        ) from exc

    return CheckoutOut(url=checkout.url, checkout_id=checkout.id)


async def change_plan(
    db: AsyncSession,
    user: User,
    tier: Tier,
    request: Request | None = None,
) -> ChangePlanOut:
    """Change the product on the user's one active Polar subscription."""
    if not _enabled():
        raise HTTPException(status_code=503, detail="Subscriptions are disabled")
    if tier == Tier.free:
        raise HTTPException(status_code=400, detail="Cannot change to the free tier")

    _configure_product_ids()
    target = get_tier_config(tier)
    if not target.polar_product_id:
        raise HTTPException(
            status_code=409, detail=f"No Polar product configured for {tier.value}"
        )

    subscription = await crud_subscription.get_by_user(db, user.id)
    if subscription is None or not subscription.polar_subscription_id:
        raise HTTPException(
            status_code=409, detail="No existing subscription to change"
        )
    if subscription.status != "active":
        raise HTTPException(
            status_code=409,
            detail="Only an active subscription can change plans; use the customer portal",
        )
    if subscription.tier == tier.value:
        raise HTTPException(status_code=409, detail="This is already the current plan")

    try:
        current_tier = Tier(subscription.tier)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="Current subscription tier is invalid"
        ) from exc
    if current_tier == Tier.free:
        raise HTTPException(
            status_code=409,
            detail="Current subscription product is unresolved; contact support",
        )

    rank = {Tier.free: 0, Tier.tier1: 1, Tier.tier2: 2}
    is_upgrade = rank[tier] > rank[current_tier]
    effective = "immediate" if is_upgrade else "next_period"

    from polar_sdk.models import SubscriptionLocked
    from polar_sdk.models.subscriptionprorationbehavior import (
        SubscriptionProrationBehavior,
    )

    polar = get_polar_client()
    try:
        updated = await polar.subscriptions.update_async(
            id=subscription.polar_subscription_id,
            subscription_update={
                "product_id": target.polar_product_id,
                "proration_behavior": (
                    SubscriptionProrationBehavior.INVOICE
                    if is_upgrade
                    else SubscriptionProrationBehavior.NEXT_PERIOD
                ),
            },
        )
    except SubscriptionLocked as exc:
        raise HTTPException(
            status_code=409, detail="The subscription is busy; retry shortly"
        ) from exc
    except Exception as exc:
        _polar_log(
            request,
            level="error",
            op="subscriptions.update",
            error=type(exc).__name__,
            user_id=user.id,
            requested_tier=tier.value,
        )
        raise HTTPException(
            status_code=502, detail="Failed to change subscription plan"
        ) from exc

    pending = getattr(updated, "pending_update", None)
    effective_at = getattr(pending, "applies_at", None) if pending else None
    return ChangePlanOut(
        requested_tier=tier.value,
        effective=effective,
        effective_at=effective_at,
    )


async def create_portal_session(
    user: User, request: Request | None = None
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
        raise HTTPException(
            status_code=502, detail="Failed to create portal session"
        ) from exc

    return PortalOut(url=session.customer_portal_url)


# ==================== Webhook handling ====================


async def handle_polar_event(
    db: AsyncSession, event: Any, request: Request | None = None
) -> int | None:
    """Dispatch a verified Polar webhook event to the right upsert path.

    `event` is the pydantic model returned by `polar_sdk.webhooks.validate_event`.
    Each payload has a `.type` string and a `.data` object.
    """
    event_type: str = getattr(event, "TYPE", None) or getattr(event, "type", "") or ""
    data: Any = getattr(event, "data", None)

    _configure_product_ids()

    if event_type in SUBSCRIPTION_EVENTS:
        return await _handle_subscription_event(
            db,
            event_type,
            data,
            event_at=_event_at(event, data, request),
            request=request,
        )
    elif event_type in CUSTOMER_EVENTS:
        await _handle_customer_event(db, data)
    else:
        # Acknowledge but ignore (checkout.*, order.*, benefit_grant.*, etc.)
        _polar_log(request, level="info", op="ignore_event", event_type=event_type)
    return None


def _coerce_dt(value: Any) -> Any | None:
    if value is None:
        return None
    return value


def _event_at(event: Any, data: Any, request: Request | None = None) -> datetime:
    """Best-effort event time for out-of-order webhook protection."""
    value = getattr(event, "timestamp", None)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
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
    request: Request | None = None,
) -> User | None:
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
        (
            await db.execute(
                select(UserModel).where(
                    UserModel.polar_customer_id == polar_customer_id
                )
            )
        )
        .scalars()
        .first()
    )
    if user is not None:
        return cast(User, user)

    # 1. Subscription row lookup (kept for resilience / older rows).
    row = (
        (
            await db.execute(
                select(UserSubscription).where(
                    UserSubscription.polar_customer_id == polar_customer_id
                )
            )
        )
        .scalars()
        .first()
    )
    if row is not None:
        user = await crud_user.get(db, id=row.user_id)
        if user is not None:
            return user

    # 2. Fall back to Polar API to discover the external id.
    polar = get_polar_client()
    if polar is None:
        return None
    from polar_sdk.models import ResourceNotFound

    try:
        customer = await polar.customers.get_async(
            id=polar_customer_id, timeout_ms=1_500
        )
    except ResourceNotFound as exc:
        _polar_log(
            request,
            level="warning",
            op="customers.get",
            polar_customer_id=polar_customer_id,
            reason="unresolved_customer",
            error=type(exc).__name__,
        )
        return None
    except Exception as exc:
        _polar_log(
            request,
            level="error",
            op="customers.get",
            polar_customer_id=polar_customer_id,
            reason="retryable_lookup_failure",
            error=type(exc).__name__,
        )
        raise
    try:
        from polar_sdk.types import UNSET

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
            await crud_subscription.set_polar_customer_id(
                db, user.id, polar_customer_id
            )
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
    *,
    event_at: datetime,
    request: Request | None = None,
) -> int | None:
    if data is None:
        return None
    polar_customer_id: str | None = getattr(data, "customer_id", None)
    if not polar_customer_id:
        _polar_log(
            request,
            level="warning",
            op="subscription_event",
            event_type=event_type,
            reason="missing_customer_id",
        )
        return None

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
            reason="unresolved_customer",
        )
        return None

    product_id: str | None = getattr(data, "product_id", None)
    # Map the event type to a status string we persist.
    status = _subscription_status_for_event(event_type, data)
    # Derive the tier we'll persist from the configured product id.
    from app.core.tiers import tier_for_product

    resolved_tier = tier_for_product(product_id)
    if resolved_tier == Tier.free:
        _polar_log(
            request,
            level="error",
            op="subscription_event",
            event_type=event_type,
            user_id=user.id,
            polar_customer_id=polar_customer_id,
            product_id=product_id,
            reason="unknown_product",
        )
        # An unmapped product never grants a tier, but cancellations and
        # revocations must still land or the old paid access would never end.
        subscription_id = getattr(data, "id", None)
        if not subscription_id or not _limits_access(status, data):
            return None
        applied = await crud_subscription.apply_lifecycle_from_webhook(
            db,
            user_id=user.id,
            polar_subscription_id=subscription_id,
            status=status,
            current_period_start=_coerce_dt(
                getattr(data, "current_period_start", None)
            ),
            current_period_end=_coerce_dt(getattr(data, "current_period_end", None)),
            canceled_at=_coerce_dt(getattr(data, "canceled_at", None)),
            cancel_at_period_end=bool(getattr(data, "cancel_at_period_end", False)),
            ends_at=_coerce_dt(getattr(data, "ends_at", None)),
            ended_at=_coerce_dt(getattr(data, "ended_at", None)),
            metadata={"event_type": event_type, "unknown_product": True},
            event_at=event_at,
            event_priority=_subscription_event_priority(event_type, status, data),
        )
        return user.id if applied is not None else None

    pending_tier: str | None = None
    pending_effective_at: datetime | None = None
    pending_update = getattr(data, "pending_update", None)
    if pending_update is not None:
        pending_product_id = getattr(pending_update, "product_id", None)
        mapped_pending = tier_for_product(pending_product_id)
        if mapped_pending != Tier.free:
            pending_tier = mapped_pending.value
            pending_effective_at = _coerce_dt(
                getattr(pending_update, "applies_at", None)
            )

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
        cancel_at_period_end=bool(getattr(data, "cancel_at_period_end", False)),
        ends_at=_coerce_dt(getattr(data, "ends_at", None)),
        ended_at=_coerce_dt(getattr(data, "ended_at", None)),
        pending_tier=pending_tier,
        pending_effective_at=pending_effective_at,
        metadata={"event_type": event_type},
        event_at=event_at,
        event_priority=_subscription_event_priority(event_type, status, data),
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
        return user.id
    return user.id


def _subscription_status_for_event(event_type: str, data: Any) -> str:
    if event_type == "subscription.revoked":
        return "revoked"
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


def _limits_access(status: str, data: Any) -> bool:
    """Whether a snapshot ends, suspends or schedules the end of paid access."""
    return (
        status not in {"active", "trialing"}
        or bool(getattr(data, "cancel_at_period_end", False))
        or getattr(data, "ends_at", None) is not None
        or getattr(data, "ended_at", None) is not None
    )


def _subscription_event_priority(event_type: str, status: str, data: Any) -> int:
    """Order equal-timestamp snapshots by their access-loss severity."""
    if (
        event_type == "subscription.revoked"
        or status in {"revoked", "unpaid"}
        or getattr(data, "ended_at", None) is not None
    ):
        return 70
    if event_type == "subscription.canceled" or bool(
        getattr(data, "cancel_at_period_end", False)
    ):
        return 60
    if status == "past_due":
        return 50
    return _EVENT_PRIORITIES.get(event_type, 0)


async def _handle_customer_event(db: AsyncSession, data: Any) -> None:
    if data is None:
        return
    polar_customer_id: str | None = getattr(data, "id", None)
    external_id: str | None = getattr(data, "external_id", None)
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
