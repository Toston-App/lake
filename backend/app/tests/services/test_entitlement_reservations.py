from datetime import datetime, timedelta, timezone

import pytest
from app.core.config import settings
from app.models.chat_observability import ChatObsTurn
from app.models.user import User
from app.services.entitlements import (
    TURN_STATUS_INTERRUPTED,
    get_period_usage,
    reserve_chat_turn,
)


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
    )

    assert quota.allowed
    assert turn_id is not None
    turn = await db_session.get(ChatObsTurn, turn_id)
    assert turn is not None
    assert turn.status == "in_progress"
    assert turn.user_message is None


async def test_messages_are_not_limited(
    db_session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_OBSERVABILITY_ENABLED", False)
    db_session.add_all(
        ChatObsTurn(
            conversation_id=f"done-{index}",
            user_id=test_user.id,
            provider="openrouter",
            status="ok",
            total_tokens=100,
        )
        for index in range(50)
    )
    await db_session.commit()

    quota, turn_id = await reserve_chat_turn(
        db_session,
        test_user,
        conversation_id="many-messages",
        user_message="hello",
    )

    assert quota.allowed
    assert turn_id is not None
    assert quota.usage.messages_used == 50
    assert quota.usage.tokens_used == 5_000
    assert quota.usage.tokens_limit == 20_000
    assert quota.usage.percent_used == 25.0


async def test_token_limit_blocks_the_turn(
    db_session, test_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_OBSERVABILITY_ENABLED", False)
    db_session.add(
        ChatObsTurn(
            conversation_id="big-turn",
            user_id=test_user.id,
            provider="openrouter",
            status="ok",
            total_tokens=25_000,
        )
    )
    await db_session.commit()

    quota, turn_id = await reserve_chat_turn(
        db_session,
        test_user,
        conversation_id="over-limit",
        user_message="hello",
    )

    assert not quota.allowed
    assert quota.reason == "token_limit_reached"
    assert turn_id is None
    assert quota.usage.percent_used == 100.0


@pytest.mark.parametrize(
    "tier,premium_model,expected",
    [
        ("free", "premium-model", "base-model"),
        ("tier1", "premium-model", "base-model"),
        ("tier2", "premium-model", "premium-model"),
        ("tier2", None, "base-model"),
    ],
)
async def test_turn_records_the_tier_model(
    db_session,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
    tier,
    premium_model,
    expected,
) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", tier)
    monkeypatch.setattr(settings, "CHAT_OBSERVABILITY_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_MODEL", "base-model")
    monkeypatch.setattr(settings, "CHAT_PREMIUM_MODEL", premium_model)

    quota, turn_id = await reserve_chat_turn(
        db_session,
        test_user,
        conversation_id=f"model-{tier}",
        user_message="hello",
    )

    assert quota.tier.value == tier
    turn = await db_session.get(ChatObsTurn, turn_id)
    assert turn is not None
    assert turn.model == expected


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
