import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from app.core.config import settings
from app.models.chat_observability import ChatObsTurn
from app.models.user import User
from app.services.entitlements import (
    TURN_STATUS_INTERRUPTED,
    get_period_usage,
    reserve_chat_turn,
)
from sqlalchemy import delete

from tests.conftest import TestSessionLocal


async def test_reservation_records_billing_when_observability_is_disabled(
    db_session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_OBSERVABILITY_ENABLED", False)

    quota, turn_id = await reserve_chat_turn(
        db_session,
        test_user,
        conversation_id="billing-only",
        user_message="sensitive text",
        model="test-model",
    )

    assert quota.allowed
    assert turn_id is not None
    turn = await db_session.get(ChatObsTurn, turn_id)
    assert turn is not None
    assert turn.status == "in_progress"
    assert turn.user_message is None


async def test_stale_reservation_does_not_consume_quota(
    db_session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_OBSERVABILITY_ENABLED", False)
    for index in range(9):
        db_session.add(
            ChatObsTurn(
                conversation_id=f"done-{index}",
                user_id=test_user.id,
                provider="openrouter",
                status="ok",
            )
        )
    db_session.add(
        ChatObsTurn(
            conversation_id="stale",
            user_id=test_user.id,
            provider="openrouter",
            status="in_progress",
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    await db_session.commit()

    quota, turn_id = await reserve_chat_turn(
        db_session,
        test_user,
        conversation_id="last-slot",
        user_message="hello",
        model="test-model",
    )
    assert quota.allowed
    assert turn_id is not None


async def test_interrupted_turns_count_and_errored_tokens_are_billed(
    db_session, test_user: User
) -> None:
    period_start = datetime.now(timezone.utc) - timedelta(days=1)
    rows = [
        ("ok", 100),
        (TURN_STATUS_INTERRUPTED, 200),
        ("error", 400),
        ("pending_confirmation", 800),
    ]
    for index, (status, tokens) in enumerate(rows):
        db_session.add(
            ChatObsTurn(
                conversation_id=f"usage-{index}",
                user_id=test_user.id,
                provider="openrouter",
                status=status,
                total_tokens=tokens,
            )
        )
    db_session.add(
        ChatObsTurn(
            conversation_id="usage-stale",
            user_id=test_user.id,
            provider="openrouter",
            status="in_progress",
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    await db_session.commit()

    usage = await get_period_usage(db_session, test_user.id, period_start)

    # A server error refunds the message but never the tokens it consumed; a
    # client disconnect is billed in full.
    assert usage == {"messages_used": 3, "tokens_used": 1500}


async def test_parallel_reservations_admit_exactly_one_final_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_OBSERVABILITY_ENABLED", False)
    email = f"quota-{uuid4()}@example.com"

    async with TestSessionLocal() as setup:
        user = User(email=email, country="USD", is_active=True, is_superuser=False)
        setup.add(user)
        await setup.flush()
        user_id = user.id
        setup.add_all(
            [
                ChatObsTurn(
                    conversation_id=f"existing-{index}",
                    user_id=user_id,
                    provider="openrouter",
                    status="ok",
                )
                for index in range(9)
            ]
        )
        await setup.commit()

    async def reserve(index: int):
        async with TestSessionLocal() as session:
            user = await session.get(User, user_id)
            assert user is not None
            return await reserve_chat_turn(
                session,
                user,
                conversation_id=f"parallel-{index}",
                user_message="hello",
                model="test-model",
            )

    try:
        results = await asyncio.gather(reserve(1), reserve(2))
        assert sum(1 for quota, _ in results if quota.allowed) == 1
        assert sum(1 for _, turn_id in results if turn_id is not None) == 1
    finally:
        async with TestSessionLocal() as cleanup:
            await cleanup.execute(
                delete(ChatObsTurn).where(ChatObsTurn.user_id == user_id)
            )
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()
