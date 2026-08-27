"""Admin usage/P&L endpoints under /api/v1/admin/observability."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.chat_observability import ChatObsTurn
from app.models.subscription import UserSubscription
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import create_test_user

PREFIX = "/api/v1/admin/observability"


def _window() -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    return start.isoformat(), end.isoformat()


async def _add_turn(
    db: AsyncSession,
    *,
    user_id: int,
    cost: str,
    tokens: int = 1000,
    started_at: datetime | None = None,
) -> None:
    db.add(
        ChatObsTurn(
            conversation_id=f"conv-{user_id}",
            user_id=user_id,
            provider="openrouter",
            status="ok",
            total_tokens=tokens,
            total_cost_usd=Decimal(cost),
            started_at=started_at or datetime.now(timezone.utc),
        )
    )
    await db.commit()


async def _add_subscription(
    db: AsyncSession,
    *,
    user_id: int,
    tier: str,
    status: str,
) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        UserSubscription(
            user_id=user_id,
            polar_customer_id=f"cust-{user_id}",
            polar_subscription_id=f"sub-{user_id}",
            tier=tier,
            status=status,
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    await db.commit()


class TestEconomicsAuth:
    async def test_unauthenticated_is_401(self, unauth_client: AsyncClient):
        resp = await unauth_client.get(f"{PREFIX}/economics")
        assert resp.status_code == 401

    async def test_regular_user_is_rejected(self, client: AsyncClient):
        resp = await client.get(f"{PREFIX}/economics")
        assert resp.status_code == 400
        assert "privileges" in resp.json()["detail"]

    async def test_regular_user_cannot_list_users(self, client: AsyncClient):
        resp = await client.get(f"{PREFIX}/users")
        assert resp.status_code == 400


class TestEconomicsNumbers:
    async def test_free_user_with_cost_is_a_loss(
        self,
        superuser_client: AsyncClient,
        db_session: AsyncSession,
    ):
        user = await create_test_user(db_session, email="free-loser@example.com")
        await _add_turn(db_session, user_id=user.id, cost="1.00")
        start, end = _window()

        summary = await superuser_client.get(
            f"{PREFIX}/economics", params={"start": start, "end": end}
        )
        assert summary.status_code == 200
        body = summary.json()
        assert body["window_days"] == 30
        assert body["losing_users"] >= 1

        users = await superuser_client.get(
            f"{PREFIX}/users",
            params={"start": start, "end": end, "search": "free-loser"},
        )
        assert users.status_code == 200
        items = users.json()["items"]
        assert len(items) == 1
        row = items[0]
        assert row["email"] == "free-loser@example.com"
        assert row["is_paid"] is False
        assert row["tier"] == "free"
        assert row["ai_cost_usd"] == 1.0
        assert row["estimated_revenue_usd"] == 0.0
        assert row["profit_usd"] == -1.0

    async def test_active_tier1_catalog_revenue(
        self,
        superuser_client: AsyncClient,
        db_session: AsyncSession,
    ):
        user = await create_test_user(db_session, email="paid-winner@example.com")
        await _add_subscription(
            db_session, user_id=user.id, tier="tier1", status="active"
        )
        await _add_turn(db_session, user_id=user.id, cost="0.10")
        start, end = _window()

        resp = await superuser_client.get(
            f"{PREFIX}/users",
            params={"start": start, "end": end, "search": "paid-winner"},
        )
        assert resp.status_code == 200
        row = resp.json()["items"][0]
        assert row["is_paid"] is True
        assert row["tier"] == "tier1"
        assert row["monthly_price_usd"] == 4.99
        assert row["ai_cost_usd"] == 0.1
        assert row["estimated_revenue_usd"] == 4.99
        assert row["profit_usd"] == 4.89

    async def test_revoked_tier1_contributes_no_revenue(
        self,
        superuser_client: AsyncClient,
        db_session: AsyncSession,
    ):
        user = await create_test_user(db_session, email="revoked@example.com")
        await _add_subscription(
            db_session, user_id=user.id, tier="tier1", status="revoked"
        )
        await _add_turn(db_session, user_id=user.id, cost="0.50")
        start, end = _window()

        resp = await superuser_client.get(
            f"{PREFIX}/users",
            params={"start": start, "end": end, "search": "revoked@"},
        )
        row = resp.json()["items"][0]
        assert row["is_paid"] is False
        assert row["tier"] == "free"
        assert row["status"] == "revoked"
        assert row["estimated_revenue_usd"] == 0.0
        assert row["profit_usd"] == -0.5

    async def test_outcome_and_search_filters(
        self,
        superuser_client: AsyncClient,
        db_session: AsyncSession,
    ):
        loser = await create_test_user(db_session, email="filter-loser@example.com")
        winner = await create_test_user(db_session, email="filter-winner@example.com")
        await _add_turn(db_session, user_id=loser.id, cost="2.00")
        await _add_subscription(
            db_session, user_id=winner.id, tier="tier2", status="active"
        )
        start, end = _window()

        losing = await superuser_client.get(
            f"{PREFIX}/users",
            params={
                "start": start,
                "end": end,
                "outcome": "losing",
                "search": "filter-",
            },
        )
        emails = {row["email"] for row in losing.json()["items"]}
        assert "filter-loser@example.com" in emails
        assert "filter-winner@example.com" not in emails

        found = await superuser_client.get(
            f"{PREFIX}/users",
            params={"start": start, "end": end, "search": "filter-winner"},
        )
        assert found.json()["count"] == 1
        assert found.json()["items"][0]["email"] == "filter-winner@example.com"
        assert found.json()["items"][0]["estimated_revenue_usd"] == 14.99
