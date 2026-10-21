"""Polar.sh webhook receiver.

Public (unauthenticated) endpoint that Polar calls to deliver subscription
lifecycle events. The payload signature is verified using
`POLAR_WEBHOOK_SECRET` (Polar `polar_whs_` or Standard Webhooks `whsec_`); on
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
from app.utilities.redis import invalidate_entitlement
from app.utilities.wide_events import enrich_event, mark_for_logging

router = APIRouter()


async def _bounded_body(request: Request) -> bytes:
    limit = settings.POLAR_WEBHOOK_MAX_BYTES
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise HTTPException(status_code=413, detail="Webhook payload too large")
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid Content-Length"
            ) from None

    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > limit:
            raise HTTPException(status_code=413, detail="Webhook payload too large")
    return bytes(payload)


def _validate_event(body: bytes, headers: dict[str, str], secret: str):
    """Verify and parse a Polar webhook, accepting both secret formats.

    `validate_event` base64-encodes the secret as text, which suits Polar's
    `polar_whs_...` secrets but not Standard Webhooks `whsec_<base64 key>`
    ones: those must be decoded to the key, so we verify them directly.
    """
    from polar_sdk.webhooks import (
        WebhookUnknownTypeError,
        WebhookVerificationError,
        WebhoookPayload,
        validate_event,
    )

    if not secret.startswith("whsec_"):
        return validate_event(body=body, headers=headers, secret=secret)

    from polar_sdk.models import WebhookEventType
    from pydantic import TypeAdapter
    from standardwebhooks.webhooks import Webhook
    from standardwebhooks.webhooks import (
        WebhookVerificationError as StandardWebhookVerificationError,
    )

    try:
        data = Webhook(secret).verify(body, headers)
    except StandardWebhookVerificationError as exc:
        raise WebhookVerificationError(str(exc)) from exc

    if isinstance(data, dict) and data.get("type") not in {
        t.value for t in WebhookEventType
    }:
        raise WebhookUnknownTypeError(data.get("type"))
    return TypeAdapter(WebhoookPayload).validate_python(data)


@router.post("/webhook")
async def polar_webhook(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
) -> Response:
    if not settings.SUBSCRIPTIONS_ENABLED:
        # Acknowledge silently when disabled so Polar doesn't keep retrying.
        return Response(status_code=202)

    payload = await _bounded_body(request)
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

    try:
        from polar_sdk.webhooks import (
            WebhookUnknownTypeError,
            WebhookVerificationError,
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
        event = _validate_event(payload, dict(request.headers), secret)
    except WebhookVerificationError:
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    except WebhookUnknownTypeError:
        # Signature was valid but the event type isn't known to this SDK
        # version. Acknowledge so Polar doesn't retry; safe to ignore.
        if webhook_id:
            await crud_subscription.claim_webhook(db, webhook_id, event_type="unknown")
            await db.commit()
        enrich_event(request, webhook={"outcome": "unknown_event_type"})
        return Response(status_code=202)
    except Exception as exc:  # malformed payload etc.
        enrich_event(request, webhook={"outcome": "error", "error": str(exc)})
        raise HTTPException(
            status_code=400, detail="Malformed webhook payload"
        ) from exc

    event_type = getattr(event, "TYPE", None) or getattr(event, "type", None)

    try:
        duplicate = bool(
            webhook_id
            and not await crud_subscription.claim_webhook(
                db,
                webhook_id,
                event_type=str(event_type) if event_type else None,
            )
        )
        affected_user_id = await handle_polar_event(db, event, request=request)
        await db.commit()
        if affected_user_id is not None and not await invalidate_entitlement(
            affected_user_id
        ):
            raise RuntimeError("entitlement cache invalidation failed")
        if duplicate:
            enrich_event(request, webhook={"outcome": "duplicate_repaired"})
            return Response(status_code=202)
    except Exception as exc:
        await db.rollback()
        # A pre-commit failure rolls the claim back. If cache repair fails after
        # commit, the claim remains and duplicate replay takes the repair path.
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
        raise HTTPException(status_code=500, detail="Webhook handler failed") from exc

    enrich_event(
        request,
        webhook={
            "outcome": "ok",
            "event_type": event_type,
        },
    )
    return Response(status_code=202)
