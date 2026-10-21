"""GET /subscriptions/me returns only what the chat client renders."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from app.core.config import settings
from app.models.chat_observability import ChatObsTurn
from app.models.subscription import UserSubscription
from app.models.user import User
from httpx import AsyncClient


async def test_me_returns_the_trimmed_shape(
    client: AsyncClient,
    db_session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(
        "app.services.entitlements.get_entitlement", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.entitlements.set_entitlement", AsyncMock(return_value=True)
    )
    now = datetime.now(timezone.utc)
    db_session.add(
        UserSubscription(
            user_id=test_user.id,
            polar_customer_id=f"customer_{test_user.id}",
            polar_subscription_id=f"subscription_{test_user.id}",
            tier="tier1",
            status="revoked",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    db_session.add(
        ChatObsTurn(
            conversation_id="usage",
            user_id=test_user.id,
            provider="openrouter",
            status="ok",
            total_tokens=13_060,
        )
    )
    await db_session.commit()

    response = await client.get("/api/v1/subscriptions/me")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"subscription", "usage", "tier_config"}
    assert set(body["subscription"]) == {
        "tier",
        "status",
        "current_period_start",
        "current_period_end",
        "pending_tier",
        "pending_effective_at",
        "can_manage",
    }
    assert body["subscription"]["tier"] == "free"
    assert body["subscription"]["status"] == "revoked"
    assert body["subscription"]["can_manage"] is True
    assert body["usage"]["percent_used"] == 65.3
    assert set(body["usage"]) == {"period_start", "period_end", "percent_used"}
    assert body["tier_config"] == {
        "tier": "free",
        "label": "Free",
        "premium_model": False,
        "available": False,
    }
