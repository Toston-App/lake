"""Polar.sh webhook receiver.

Public (unauthenticated) endpoint that Polar calls to deliver subscription
lifecycle events. The payload signature is verified with
`polar_sdk.webhooks.validate_event` using `POLAR_WEBHOOK_SECRET`; on
verification failure we return 403. Unknown event types are acknowledged
with 202 so Polar doesn't retry (forward compatibility).

Handler failures return 500 so Polar retries. Successfully processed
Standard Webhooks `webhook-id` values are stored to skip duplicates.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.crud import crud_subscription
from app.services.polar import handle_polar_event
from app.utilities.wide_events import enrich_event, mark_for_logging

router = APIRouter()


@router.post("/webhook")
async def polar_webhook(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
):
    if not settings.SUBSCRIPTIONS_ENABLED:
        # Acknowledge silently when disabled so Polar doesn't keep retrying.
        return Response(status_code=202)

    payload = await request.body()
    webhook_id = request.headers.get("webhook-id")

    mark_for_logging(request)
    enrich_event(
        request,
        webhook={
            "type": "polar",
            "size": len(payload),
            "webhook_id": webhook_id,
        },
    )

    if webhook_id and await crud_subscription.is_webhook_processed(db, webhook_id):
        enrich_event(request, webhook={"outcome": "duplicate"})
        return Response(status_code=202)

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

    secret = (settings.POLAR_WEBHOOK_SECRET or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    try:
        event = validate_event(
            body=payload,
            headers=dict(request.headers),
            secret=secret,
        )
    except WebhookVerificationError:
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    except WebhookUnknownTypeError:
        # Signature was valid but the event type isn't known to this SDK
        # version. Acknowledge so Polar doesn't retry; safe to ignore.
        if webhook_id:
            await crud_subscription.mark_webhook_processed(
                db, webhook_id, event_type="unknown"
            )
        enrich_event(request, webhook={"outcome": "unknown_event_type"})
        return Response(status_code=202)
    except Exception as exc:  # malformed payload etc.
        enrich_event(request, webhook={"outcome": "error", "error": str(exc)})
        raise HTTPException(status_code=400, detail="Malformed webhook payload") from exc

    event_type = getattr(event, "TYPE", None) or getattr(event, "type", None)

    try:
        await handle_polar_event(db, event, request=request)
    except Exception as exc:
        # 5xx so Polar retries; do not mark webhook_id as processed.
        enrich_event(
            request,
            webhook={
                "outcome": "handler_error",
                "event_type": event_type,
                "polar_customer_id": getattr(
                    getattr(event, "data", None), "customer_id", None
                )
                or getattr(getattr(event, "data", None), "id", None),
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=500, detail="Webhook handler failed"
        ) from exc

    if webhook_id:
        await crud_subscription.mark_webhook_processed(
            db, webhook_id, event_type=str(event_type) if event_type else None
        )

    enrich_event(
        request,
        webhook={
            "outcome": "ok",
            "event_type": event_type,
        },
    )
    return Response(status_code=202)
