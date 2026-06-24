"""Polar.sh webhook receiver.

Public (unauthenticated) endpoint that Polar calls to deliver subscription
lifecycle events. The payload signature is verified with
`polar_sdk.webhooks.validate_event` using `POLAR_WEBHOOK_SECRET`; on
verification failure we return 403. Unknown event types are acknowledged
with 202 so Polar doesn't retry (forward compatibility).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.services.polar import handle_polar_event
from app.utilities.wide_events import enrich_event

router = APIRouter()


@router.post("/webhook")
async def polar_webhook(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
):
    if not settings.SUBSCRIPTIONS_ENABLED:
        # Acknowledge silently when disabled so Polar doesn't keep retrying.
        return {"ok": False, "reason": "disabled"}

    payload = await request.body()

    enrich_event(
        request,
        webhook={
            "type": "polar",
            "size": len(payload),
        },
    )

    try:
        from polar_sdk.webhooks import (  # type: ignore[import-not-found]
            WebhookUnknownTypeError,
            WebhookVerificationError,
            validate_event,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="polar-sdk is not installed; cannot process webhooks",
        ) from exc

    try:
        event = validate_event(
            payload=payload,
            headers=dict(request.headers),
            secret=settings.POLAR_WEBHOOK_SECRET or "",
        )
    except WebhookVerificationError:
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    except WebhookUnknownTypeError:
        # Signature was valid but the event type isn't known to this SDK
        # version. Acknowledge so Polar doesn't retry; safe to ignore.
        enrich_event(request, webhook={"outcome": "unknown_event_type"})
        return "", 202
    except Exception as exc:  # malformed payload etc.
        enrich_event(request, webhook={"outcome": "error", "error": str(exc)})
        raise HTTPException(status_code=400, detail="Malformed webhook payload") from exc

    try:
        await handle_polar_event(db, event)
    except Exception as exc:
        # Log but still 202 so Polar doesn't retry on our internal errors
        # (we'd rather investigate than have Polar redeliver a storm).
        enrich_event(
            request,
            webhook={
                "outcome": "handler_error",
                "event_type": getattr(event, "type", None),
                "error": str(exc),
            },
        )
        return "", 202

    enrich_event(
        request,
        webhook={
            "outcome": "ok",
            "event_type": getattr(event, "type", None),
        },
    )
    return "", 202
