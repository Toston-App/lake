"""Subscription, tier and checkout endpoints for the chat client."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.core.tiers import (
    Tier,
    TierConfig,
    get_tier_config,
    tier_from_value,
)
from app.models.user import User
from app.schemas.subscription import (
    ChangePlanOut,
    CheckoutOut,
    CheckoutRequest,
    PortalOut,
    SubscriptionMeOut,
    SubscriptionOut,
    TierOut,
)
from app.services.entitlements import check_quota
from app.services.polar import change_plan, create_checkout, create_portal_session
from app.utilities.wide_events import enrich_event

router = APIRouter()


def _tier_to_out(cfg: TierConfig) -> TierOut:
    return TierOut(
        tier=cfg.tier.value,
        label=cfg.label,
        monthly_message_limit=cfg.monthly_message_limit,
        monthly_token_limit=cfg.monthly_token_limit,
        features=sorted(cfg.features),
        price_hint=cfg.price_hint,
        available=bool(cfg.polar_product_id) or not settings.SUBSCRIPTIONS_ENABLED,
    )


@router.get("/tiers", response_model=list[TierOut])
async def list_tiers(
    _: User = Depends(deps.get_current_active_user),
) -> Any:
    """List all subscription tiers and their limits/features."""
    # Refresh product ids from env so `available` reflects current config.
    if settings.SUBSCRIPTIONS_ENABLED:
        from app.services.polar import _configure_product_ids

        _configure_product_ids()
    return [_tier_to_out(get_tier_config(t)) for t in Tier]


@router.get("/me", response_model=SubscriptionMeOut)
async def get_my_subscription(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Return the caller's current tier, usage for the billing period, and
    the tier's feature/limit configuration."""
    quota = await check_quota(db, current_user)

    # Build the subscription summary. When subscriptions are disabled (dev
    # mode) we synthesize an active entry from the dev-default tier.
    if not settings.SUBSCRIPTIONS_ENABLED:
        sub = SubscriptionOut(
            tier=quota.tier.value,
            status="active",
            current_period_start=quota.period_start,
            current_period_end=None,
            can_manage=False,
        )
    else:
        from app.crud import crud_subscription

        row = await crud_subscription.get_by_user(db, current_user.id)
        can_manage = bool(
            current_user.polar_customer_id or (row and row.polar_customer_id)
        )
        sub = SubscriptionOut(
            tier=quota.tier.value,
            status=row.status if row else "free",
            current_period_start=row.current_period_start
            if row
            else quota.period_start,
            current_period_end=row.current_period_end if row else None,
            cancel_at_period_end=bool(row and row.cancel_at_period_end),
            ends_at=row.ends_at if row else None,
            ended_at=row.ended_at if row else None,
            pending_tier=row.pending_tier if row else None,
            pending_effective_at=row.pending_effective_at if row else None,
            can_manage=can_manage,
        )

    enrich_event(
        request,
        user={
            "id": current_user.id,
            "email": current_user.email,
            "subscription_tier": quota.tier.value,
        },
        operation={"type": "subscription_me"},
    )

    return SubscriptionMeOut(
        subscription=sub,
        usage=quota.usage,
        tier_config=_tier_to_out(get_tier_config(quota.tier)),
    )


@router.post("/checkout", response_model=CheckoutOut)
async def create_subscription_checkout(
    request: Request,
    body: CheckoutRequest,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Create a Polar checkout session for the requested tier.

    Returns a hosted URL the frontend should redirect to.
    """
    try:
        tier = tier_from_value(body.tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {body.tier}")
    if tier == Tier.free:
        raise HTTPException(status_code=400, detail="Cannot subscribe to the free tier")
    if not settings.SUBSCRIPTIONS_ENABLED:
        raise HTTPException(status_code=503, detail="Subscriptions are disabled")

    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "subscription_checkout", "tier": tier.value},
    )
    return await create_checkout(db, current_user, tier, request=request)


@router.post("/change-plan", response_model=ChangePlanOut)
async def change_subscription_plan(
    request: Request,
    body: CheckoutRequest,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    try:
        tier = tier_from_value(body.tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {body.tier}")

    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "subscription_change_plan", "tier": tier.value},
    )
    return await change_plan(db, current_user, tier, request=request)


@router.post("/portal", response_model=PortalOut)
async def create_subscription_portal(
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Create a Polar customer-portal session so the user can manage their
    subscription (update payment, cancel, etc.)."""
    if not settings.SUBSCRIPTIONS_ENABLED:
        raise HTTPException(status_code=503, detail="Subscriptions are disabled")

    enrich_event(
        request,
        user={"id": current_user.id, "email": current_user.email},
        operation={"type": "subscription_portal"},
    )
    return await create_portal_session(current_user, request=request)
