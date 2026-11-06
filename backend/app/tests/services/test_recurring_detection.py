"""Recurring detection against a real database: skipping, accepting, AI and access."""

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from app.ai import recurring_detection as ai
from app.core.config import settings
from app.models import (
    Expense,
    RecurringDetectionUsage,
    RecurringMerchantAlias,
    RecurringTransaction,
    User,
)
from app.services import recurring as recurring_service
from app.services import recurring_detection as detection
from app.services.errors import ConflictError, NotFoundError
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from tests.utils import create_test_account, create_test_expense, create_test_user

TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "free")
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.services.accounts.invalidate_user_cache", AsyncMock(return_value=True)
    )


@pytest.fixture
def paid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "tier1")


@pytest.fixture
def ai_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", True)


def monthly_dates(day: int, count: int, today: date = TODAY) -> list[date]:
    """`day` of the last `count` months, the newest on or before today."""
    year, month = today.year, today.month
    if day > today.day:
        month -= 1
    out = []
    for _ in range(count):
        if month == 0:
            year, month = year - 1, 12
        out.append(date(year, month, day))
        month -= 1
    return sorted(out)


async def add_series(
    db: AsyncSession,
    user: User,
    *,
    description: str = "NETFLIX.COM 8473",
    amount: float = 15.99,
    day: int = 10,
    count: int = 6,
    account_id: int | None = None,
) -> list[Expense]:
    return [
        await create_test_expense(
            db,
            owner_id=user.id,
            amount=amount,
            expense_date=d,
            description=description,
            account_id=account_id,
        )
        for d in monthly_dates(day, count)
    ]


def accept_item(
    suggestion: schemas.RecurringSuggestion, **overrides
) -> schemas.RecurringSuggestionAccept:
    data = {
        "fingerprint": suggestion.fingerprint,
        "transaction_type": suggestion.type.value,
        "transaction_id": suggestion.occurrences[0].transaction_id,
        "frequency": suggestion.frequency,
        "interval": suggestion.interval,
        "month_days": suggestion.month_days,
        "business_day_adjustment": suggestion.business_day_adjustment,
    }
    data.update(overrides)
    return schemas.RecurringSuggestionAccept(**data)


def _payload(messages) -> list[dict]:
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart):
                return json.loads(part.content)
    raise AssertionError("no prompt")


def merchant_model(mapping: dict[str, str], calls: list) -> FunctionModel:
    def fn(messages, info: AgentInfo) -> ModelResponse:
        items = _payload(messages)
        calls.append(items)
        out = [
            {
                "i": item["i"],
                "merchant_key": mapping.get(item["text"], item["text"]),
                "merchant_name": mapping.get(item["text"], item["text"]).title(),
            }
            for item in items
        ]
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"items": out})]
        )

    return FunctionModel(fn)


def label_model(kind: str, is_commitment: bool, confidence: float) -> FunctionModel:
    def fn(messages, info: AgentInfo) -> ModelResponse:
        out = [
            {
                "i": item["i"],
                "kind": kind,
                "is_commitment": is_commitment,
                "confidence": confidence,
            }
            for item in _payload(messages)
        ]
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"items": out})]
        )

    return FunctionModel(fn)


def failing_model() -> FunctionModel:
    def fn(messages, info):  # noqa: ARG001
        raise TimeoutError("model timed out")

    return FunctionModel(fn)


# -- skipping ------------------------------------------------------------------
async def test_transactions_in_a_series_are_ignored(
    db_session: AsyncSession, test_user: User
):
    expenses = await add_series(db_session, test_user)
    template = RecurringTransaction(
        owner_id=test_user.id,
        type="expense",
        amount=1,
        frequency="daily",
        start_date=TODAY,
        is_active=False,
    )
    db_session.add(template)
    await db_session.flush()
    for expense in expenses:
        expense.recurring_id = template.id
    await db_session.commit()

    scan = await detection.detect(db_session, test_user)
    assert scan.result.scanned_transactions == 0
    assert scan.result.suggestions == []


async def test_series_matching_a_paused_template_is_skipped(
    db_session: AsyncSession, test_user: User
):
    await add_series(db_session, test_user)
    [found] = (await detection.detect(db_session, test_user)).result.suggestions
    assert found.merchant_name == "Netflix"

    db_session.add(
        RecurringTransaction(
            owner_id=test_user.id,
            type="expense",
            amount=15.99,
            description="Netflix",
            frequency="monthly",
            start_date=TODAY,
            is_active=False,
        )
    )
    await db_session.commit()
    assert (await detection.detect(db_session, test_user)).result.suggestions == []


async def test_dismissed_suggestion_is_skipped_until_undone(
    db_session: AsyncSession, test_user: User
):
    await add_series(db_session, test_user)
    [found] = (await detection.detect(db_session, test_user)).result.suggestions
    item = schemas.RecurringSuggestionDismissItem(
        fingerprint=found.fingerprint, label="Netflix · monthly"
    )

    assert await detection.dismiss_suggestions(db_session, test_user, [item]) == 1
    assert await detection.dismiss_suggestions(db_session, test_user, [item]) == 0
    assert (await detection.detect(db_session, test_user)).result.suggestions == []

    await detection.undo_dismissal(db_session, test_user, found.fingerprint)
    assert len((await detection.detect(db_session, test_user)).result.suggestions) == 1
    with pytest.raises(NotFoundError):
        await detection.undo_dismissal(db_session, test_user, found.fingerprint)


# -- accepting -----------------------------------------------------------------
async def test_accept_creates_a_template_without_back_dated_copies(
    db_session: AsyncSession, test_user: User
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    expenses = await add_series(db_session, test_user, account_id=account.id)
    [found] = (await detection.detect(db_session, test_user)).result.suggestions
    assert found.occurrences[0].transaction_id == expenses[-1].id

    [result] = await detection.accept_suggestions(
        db_session, test_user, [accept_item(found)]
    )
    assert result.status == "created", result.error
    template = await db_session.get(RecurringTransaction, result.recurring.id)
    assert template.detection_fingerprint == found.fingerprint
    assert template.source_transaction_id == expenses[-1].id
    assert template.next_run_date > TODAY
    linked = await db_session.execute(
        select(Expense.id).where(Expense.recurring_id == template.id)
    )
    assert linked.scalars().all() == [expenses[-1].id]


async def test_accept_snaps_a_moved_charge_to_its_rule_date(
    db_session: AsyncSession, test_user: User
):
    # The last charge came two days early; the series is due on the 15th.
    latest = monthly_dates(15, 1, TODAY - timedelta(days=40))[0]
    expense = await create_test_expense(
        db_session,
        owner_id=test_user.id,
        amount=9.99,
        expense_date=latest - timedelta(days=2),
        description="spotify",
    )
    item = schemas.RecurringSuggestionAccept(
        fingerprint="f" * 32,
        transaction_type="expense",
        transaction_id=expense.id,
        frequency="monthly",
        month_days=[15],
    )
    [result] = await detection.accept_suggestions(db_session, test_user, [item])
    assert result.status == "created", result.error
    template = await db_session.get(RecurringTransaction, result.recurring.id)
    assert template.next_nominal_date.month != latest.month


async def test_accept_with_an_older_transaction_fails_only_that_item(
    db_session: AsyncSession, test_user: User
):
    await add_series(db_session, test_user)
    await add_series(db_session, test_user, description="Spotify", amount=9.99, day=20)
    netflix, spotify = sorted(
        (await detection.detect(db_session, test_user)).result.suggestions,
        key=lambda s: s.merchant_name,
    )
    older = netflix.occurrences[2].transaction_id

    results = await detection.accept_suggestions(
        db_session,
        test_user,
        [accept_item(netflix, transaction_id=older), accept_item(spotify)],
    )
    assert [r.status for r in results] == ["error", "created"]
    assert "latest transaction" in results[0].error


async def test_accept_fails_when_the_transaction_is_linked_after_the_check(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    """A concurrent mark-as-recurring links the transaction after it was checked."""
    await add_series(db_session, test_user)
    await add_series(db_session, test_user, description="Spotify", amount=9.99, day=20)
    netflix, spotify = sorted(
        (await detection.detect(db_session, test_user)).result.suggestions,
        key=lambda s: s.merchant_name,
    )
    contested = netflix.occurrences[0].transaction_id
    winner = await recurring_service.create_recurring(
        db_session,
        test_user,
        schemas.RecurringTransactionCreate(
            type="expense",
            amount=15.99,
            frequency="monthly",
            start_date=TODAY + timedelta(days=1),
        ),
    )
    get_expense = crud.expense.get

    async def linked_meanwhile(db, transaction_id):
        transaction = await get_expense(db, transaction_id)
        if transaction_id == contested:
            # The loaded transaction keeps its stale, unlinked state.
            await db.execute(
                update(Expense)
                .where(Expense.id == contested)
                .values(recurring_id=winner.id)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
        return transaction

    monkeypatch.setattr(crud.expense, "get", linked_meanwhile)

    results = await detection.accept_suggestions(
        db_session, test_user, [accept_item(netflix), accept_item(spotify)]
    )
    assert [r.status for r in results] == ["error", "created"]
    assert "already belongs" in results[0].error
    templates = await db_session.execute(
        select(RecurringTransaction.id).where(
            RecurringTransaction.owner_id == test_user.id,
            RecurringTransaction.detection_fingerprint == netflix.fingerprint,
        )
    )
    assert templates.scalars().all() == []
    expense = await db_session.get(Expense, contested, populate_existing=True)
    assert expense.recurring_id == winner.id


async def test_accepted_suggestion_stays_hidden_after_rename(
    db_session: AsyncSession, test_user: User
):
    await add_series(db_session, test_user)
    [found] = (await detection.detect(db_session, test_user)).result.suggestions
    [result] = await detection.accept_suggestions(
        db_session, test_user, [accept_item(found)]
    )
    await db_session.execute(
        update(RecurringTransaction)
        .where(RecurringTransaction.id == result.recurring.id)
        .values(description="Movies")
    )
    await db_session.commit()

    assert (await detection.detect(db_session, test_user)).result.suggestions == []
    [again] = await detection.accept_suggestions(
        db_session, test_user, [accept_item(found)]
    )
    assert again.status == "error"


# -- AI --------------------------------------------------------------------------
async def test_ai_groups_merchants_and_caches_aliases(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
):
    dates = monthly_dates(10, 6)
    for i, day in enumerate(dates):
        await create_test_expense(
            db_session,
            owner_id=test_user.id,
            amount=15.99,
            expense_date=day,
            description="NETFLIX.COM 8473" if i % 2 else "netflix subscription",
        )
    calls: list = []
    mapping = {"netflix": "netflix", "netflix subscription": "netflix"}
    with (
        ai.merchant_agent.override(model=merchant_model(mapping, calls)),
        ai.classify_agent.override(model=label_model("subscription", True, 0.9)),
    ):
        first = await detection.detect(db_session, test_user)
        second = await detection.detect(db_session, test_user)

    [found] = first.result.suggestions
    assert len(found.occurrences) == 6
    assert found.kind == "subscription"
    assert first.result.ai_used
    assert len(calls) == 1  # the second scan used the cached aliases
    assert len(second.result.suggestions) == 1
    aliases = await db_session.execute(
        select(RecurringMerchantAlias.source).where(
            RecurringMerchantAlias.owner_id == test_user.id
        )
    )
    assert set(aliases.scalars()) == {"ai"}


async def test_ai_failure_falls_back_and_is_not_cached(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
):
    await add_series(db_session, test_user)
    with (
        ai.merchant_agent.override(model=failing_model()),
        ai.classify_agent.override(model=failing_model()),
    ):
        scan = await detection.detect(db_session, test_user)

    assert len(scan.result.suggestions) == 1
    assert scan.result.ai_used is False
    assert scan.result.suggestions[0].kind is None
    aliases = await db_session.execute(select(RecurringMerchantAlias.id))
    assert aliases.scalars().all() == []


async def test_habit_with_high_confidence_is_dropped(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
):
    await add_series(db_session, test_user, description="Starbucks")
    with (
        ai.merchant_agent.override(model=merchant_model({}, [])),
        ai.classify_agent.override(model=label_model("habit", False, 0.95)),
    ):
        scan = await detection.detect(db_session, test_user)
    assert scan.result.suggestions == []
    assert scan.candidates == 1


async def test_no_transaction_is_open_during_ai_calls(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
):
    """An AI call can take seconds: the session must not hold a connection."""
    await add_series(db_session, test_user)
    in_transaction: dict[str, list[bool]] = {"merchants": [], "classify": []}
    real_resolve, real_classify = ai.resolve_merchants, ai.classify_candidates

    async def resolve(items, stats):
        in_transaction["merchants"].append(db_session.in_transaction())
        return await real_resolve(items, stats)

    async def classify(items, stats):
        in_transaction["classify"].append(db_session.in_transaction())
        return await real_classify(items, stats)

    monkeypatch.setattr(ai, "resolve_merchants", resolve)
    monkeypatch.setattr(ai, "classify_candidates", classify)
    with (
        ai.merchant_agent.override(model=merchant_model({}, [])),
        ai.classify_agent.override(model=label_model("subscription", True, 0.9)),
    ):
        scan = await detection.run_scan(db_session, test_user)

    assert in_transaction == {"merchants": [False], "classify": [False]}
    [found] = scan.result.suggestions
    assert found.kind == "subscription"
    assert scan.result.ai_used
    # The aliases the AI resolved were still saved, and the scan was counted.
    aliases = await db_session.execute(
        select(RecurringMerchantAlias.id).where(
            RecurringMerchantAlias.owner_id == test_user.id
        )
    )
    assert len(aliases.scalars().all()) == 1
    assert (await _usage(db_session, test_user)).scan_count == 1


def recording_model(prompts: list[str], inner: FunctionModel) -> FunctionModel:
    """Wrap a model and keep the raw prompt text it was sent."""

    def fn(messages, info: AgentInfo) -> ModelResponse:
        for message in reversed(messages):
            parts = [p for p in message.parts if isinstance(p, UserPromptPart)]
            if parts:
                prompts.append(parts[-1].content)
                break
        return inner.function(messages, info)

    return FunctionModel(fn)


def empty_model() -> FunctionModel:
    def fn(messages, info: AgentInfo) -> ModelResponse:  # noqa: ARG001
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"items": []})]
        )

    return FunctionModel(fn)


async def test_ai_prompts_are_pinned(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
):
    """The JSON sent to the model is a contract with the prompt: keep it exact.

    One series, so the order of loaded points can't change the payload.
    """
    await add_series(db_session, test_user, description="Café Ñandú", amount=4.5)
    merchant_prompts: list[str] = []
    classify_prompts: list[str] = []
    with (
        ai.merchant_agent.override(
            model=recording_model(
                merchant_prompts, merchant_model({"cafe nandu": "café ñandú"}, [])
            )
        ),
        ai.classify_agent.override(
            model=recording_model(
                classify_prompts, label_model("subscription", True, 0.9)
            )
        ),
    ):
        await detection.detect(db_session, test_user)

    assert merchant_prompts == [
        '[{"i": 0, "type": "expense", "text": "cafe nandu", "place": null}]'
    ]
    assert classify_prompts == [
        '[{"i": 0, "type": "expense", "merchant_name": "Café Ñandú", '
        '"category_name": null, "frequency": "monthly", "interval": 1, '
        '"occurrences": 6, "amount_min": 4.5, "amount_max": 4.5}]'
    ]


async def test_classify_failure_keeps_the_deterministic_result(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
):
    await add_series(db_session, test_user)
    baseline = await detection.detect(db_session, test_user, use_ai=False)
    with (
        ai.merchant_agent.override(model=merchant_model({}, [])),
        ai.classify_agent.override(model=failing_model()),
    ):
        scan = await detection.detect(db_session, test_user)

    [found] = scan.result.suggestions
    [expected] = baseline.result.suggestions
    assert found.kind is None
    assert found.confidence == expected.confidence
    assert scan.stats.calls == 2
    assert scan.stats.errors == 1
    assert scan.result.ai_used is False


async def test_empty_classify_answers_are_not_a_failure(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
):
    await add_series(db_session, test_user)
    baseline = await detection.detect(db_session, test_user, use_ai=False)
    with (
        ai.merchant_agent.override(model=merchant_model({}, [])),
        ai.classify_agent.override(model=empty_model()),
    ):
        scan = await detection.detect(db_session, test_user)

    [found] = scan.result.suggestions
    assert found.kind is None
    assert found.confidence == baseline.result.suggestions[0].confidence
    assert scan.stats.calls == 2
    assert scan.stats.errors == 0
    assert scan.result.ai_used is True


async def test_habit_commitment_is_reported_as_other(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
):
    await add_series(db_session, test_user, description="Gym membership")
    with (
        ai.merchant_agent.override(model=merchant_model({}, [])),
        ai.classify_agent.override(model=label_model("habit", True, 0.9)),
    ):
        scan = await detection.detect(db_session, test_user)
    [found] = scan.result.suggestions
    assert found.kind == "other"


def test_classify_detects_failure_without_diffing_error_counts():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(detection._classify))
    stats_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "errors"
        and isinstance(node.value, ast.Name)
        and node.value.id == "stats"
    ]
    assert stats_reads == []
    suggestion = inspect.getsource(detection._to_suggestion)
    assert "habit" not in suggestion


# -- access ----------------------------------------------------------------------
async def _usage(db: AsyncSession, user: User) -> RecurringDetectionUsage:
    result = await db.execute(
        select(RecurringDetectionUsage).where(
            RecurringDetectionUsage.owner_id == user.id
        )
    )
    usage = result.scalar_one()
    await db.refresh(usage)
    return usage


async def test_free_user_gets_one_scan(db_session: AsyncSession, test_user: User):
    await add_series(db_session, test_user)
    assert await detection.scans_remaining(db_session, test_user) == 1

    scan = await detection.run_scan(db_session, test_user)
    assert len(scan.result.suggestions) == 1
    assert scan.result.scans_remaining == 0
    assert await detection.scans_remaining(db_session, test_user) == 0

    with pytest.raises(detection.ScanLimitError) as exc:
        await detection.run_scan(db_session, test_user)
    assert exc.value.detail()["reason"] == "recurring_detection_limit"
    assert exc.value.detail()["last_scan_at"] is not None


async def test_free_scan_without_suggestions_does_not_count(
    db_session: AsyncSession, test_user: User
):
    scan = await detection.run_scan(db_session, test_user)
    assert scan.result.suggestions == []
    assert scan.result.scans_remaining == 1

    await add_series(db_session, test_user)
    scan = await detection.run_scan(db_session, test_user)
    assert len(scan.result.suggestions) == 1
    assert (await _usage(db_session, test_user)).scan_count == 1


async def test_failed_scan_does_not_use_the_free_scan(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    await add_series(db_session, test_user)
    monkeypatch.setattr(detection, "detect", AsyncMock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        await detection.run_scan(db_session, test_user)
    usage = await _usage(db_session, test_user)
    assert (usage.status, usage.scan_count) == ("done", 0)

    monkeypatch.undo()
    monkeypatch.setattr(settings, "RECURRING_DETECTION_AI_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "free")
    scan = await detection.run_scan(db_session, test_user)
    assert len(scan.result.suggestions) == 1


async def test_running_scan_blocks_another_until_it_goes_stale(
    db_session: AsyncSession, test_user: User
):
    await detection.claim_scan(db_session, test_user, unlimited=False)
    with pytest.raises(ConflictError):
        await detection.claim_scan(db_session, test_user, unlimited=False)

    await db_session.execute(
        update(RecurringDetectionUsage)
        .where(RecurringDetectionUsage.owner_id == test_user.id)
        .values(claimed_at=datetime.now(timezone.utc) - timedelta(minutes=11))
    )
    await db_session.commit()
    await detection.claim_scan(db_session, test_user, unlimited=False)


async def test_paid_tiers_scan_without_limit(
    db_session: AsyncSession,
    test_user: User,
    paid: None,  # noqa: ARG001
):
    await add_series(db_session, test_user)
    for _ in range(3):
        scan = await detection.run_scan(db_session, test_user)
        assert scan.result.scans_remaining is None
    assert await detection.scans_remaining(db_session, test_user) is None


async def test_lapsed_paid_user_who_scanned_has_no_free_scan(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    await add_series(db_session, test_user)
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "tier2")
    await detection.run_scan(db_session, test_user)

    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "free")
    with pytest.raises(detection.ScanLimitError):
        await detection.run_scan(db_session, test_user)


async def test_last_result_hides_accepted_and_dismissed(
    db_session: AsyncSession, test_user: User
):
    with pytest.raises(NotFoundError):
        await detection.last_result(db_session, test_user)

    await add_series(db_session, test_user)
    await add_series(db_session, test_user, description="Spotify", amount=9.99, day=20)
    await add_series(db_session, test_user, description="Gym", amount=30, day=3)
    scan = await detection.run_scan(db_session, test_user)
    by_name = {s.merchant_name: s for s in scan.result.suggestions}
    assert set(by_name) == {"Netflix", "Spotify", "Gym"}

    await detection.accept_suggestions(
        db_session, test_user, [accept_item(by_name["Netflix"])]
    )
    await detection.dismiss_suggestions(
        db_session,
        test_user,
        [
            schemas.RecurringSuggestionDismissItem(
                fingerprint=by_name["Gym"].fingerprint
            )
        ],
    )
    last = await detection.last_result(db_session, test_user)
    assert [s.merchant_name for s in last.suggestions] == ["Spotify"]
    assert last.scans_remaining == 0


async def test_other_users_transactions_are_not_scanned(
    db_session: AsyncSession, test_user: User
):
    other = await create_test_user(db_session, email="other@example.com")
    await add_series(db_session, other)
    scan = await detection.detect(db_session, test_user)
    assert scan.result.scanned_transactions == 0


# -- review fixes ------------------------------------------------------------------
async def test_income_and_transfer_series_are_detected(
    db_session: AsyncSession, test_user: User
):
    from tests.utils import create_test_income, create_test_transfer

    checking = await create_test_account(db_session, owner_id=test_user.id)
    savings = await create_test_account(db_session, owner_id=test_user.id)
    for day in monthly_dates(1, 6):
        await create_test_income(
            db_session,
            owner_id=test_user.id,
            amount=3000,
            income_date=day,
            description="ACME PAYROLL",
        )
        await create_test_transfer(
            db_session,
            owner_id=test_user.id,
            from_acc=checking.id,
            to_acc=savings.id,
            amount=500,
            transfer_date=day + timedelta(days=1),
            description="ahorro",
        )
    suggestions = (await detection.detect(db_session, test_user)).result.suggestions
    assert {(s.type.value, s.frequency.value) for s in suggestions} == {
        ("income", "monthly"),
        ("transfer", "monthly"),
    }
    transfer = next(s for s in suggestions if s.type.value == "transfer")
    assert (transfer.account_id, transfer.to_account_id) == (checking.id, savings.id)


async def test_accept_after_a_price_change_needs_the_latest_charge(
    db_session: AsyncSession, test_user: User
):
    dates = monthly_dates(10, 7)
    expenses = [
        await create_test_expense(
            db_session,
            owner_id=test_user.id,
            amount=9.99 if i < 4 else 12.99,
            expense_date=day,
            description="Netflix",
        )
        for i, day in enumerate(dates)
    ]
    [found] = (await detection.detect(db_session, test_user)).result.suggestions
    assert len(found.occurrences) == 7

    last_old_price = expenses[3].id
    [result] = await detection.accept_suggestions(
        db_session, test_user, [accept_item(found, transaction_id=last_old_price)]
    )
    assert result.status == "error"
    assert "latest transaction" in result.error


async def test_accept_skips_missed_dates_instead_of_back_filling(
    db_session: AsyncSession, test_user: User
):
    # Monthly series whose last charge is ~1.3 periods old: one date missed.
    last = TODAY - timedelta(days=40)
    for months_back in range(6):
        await create_test_expense(
            db_session,
            owner_id=test_user.id,
            amount=20,
            expense_date=last - timedelta(days=30 * months_back),
            description="Disney Plus",
        )
    [found] = (await detection.detect(db_session, test_user)).result.suggestions
    assert found.next_expected_date >= TODAY
    [result] = await detection.accept_suggestions(
        db_session, test_user, [accept_item(found)]
    )
    assert result.status == "created", result.error
    linked = await db_session.execute(
        select(Expense.date).where(Expense.recurring_id == result.recurring.id)
    )
    assert linked.scalars().all() == [last]


async def test_accepting_one_plan_keeps_the_merchants_other_plan(
    db_session: AsyncSession, test_user: User
):
    await add_series(
        db_session, test_user, description="Apple Bill", amount=2.99, day=5
    )
    await add_series(
        db_session, test_user, description="Apple Bill", amount=29.99, day=20
    )
    found = (await detection.detect(db_session, test_user)).result.suggestions
    assert sorted(s.amount for s in found) == [2.99, 29.99]
    cheap = next(s for s in found if s.amount == 2.99)
    [result] = await detection.accept_suggestions(
        db_session, test_user, [accept_item(cheap)]
    )
    assert result.status == "created", result.error

    [left] = (await detection.detect(db_session, test_user)).result.suggestions
    assert left.amount == 29.99


async def test_a_template_covers_only_its_closest_series(
    db_session: AsyncSession, test_user: User
):
    await add_series(
        db_session, test_user, description="Apple Bill", amount=2.99, day=5
    )
    await add_series(
        db_session, test_user, description="Apple Bill", amount=29.99, day=20
    )
    db_session.add(
        RecurringTransaction(
            owner_id=test_user.id,
            type="expense",
            amount=30,
            description="apple bill",
            frequency="monthly",
            start_date=TODAY,
        )
    )
    await db_session.commit()
    [left] = (await detection.detect(db_session, test_user)).result.suggestions
    assert left.amount == 2.99


async def test_ai_used_is_false_without_any_call(
    db_session: AsyncSession,
    test_user: User,
    ai_enabled: None,  # noqa: ARG001
):
    scan = await detection.detect(db_session, test_user)
    assert scan.result.ai_used is False


async def test_failed_save_releases_the_scan(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    await add_series(db_session, test_user)
    real_finish = detection._finish_scan
    calls = {"n": 0}

    async def flaky(db, user_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return await real_finish(db, user_id, **kwargs)

    monkeypatch.setattr(detection, "_finish_scan", flaky)
    with pytest.raises(RuntimeError):
        await detection.run_scan(db_session, test_user)
    usage = await _usage(db_session, test_user)
    assert (usage.status, usage.scan_count) == ("done", 0)


async def test_tier2_scans_without_limit(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "SUBSCRIPTIONS_DEV_DEFAULT_TIER", "tier2")
    await add_series(db_session, test_user)
    for _ in range(2):
        scan = await detection.run_scan(db_session, test_user)
        assert scan.result.scans_remaining is None


async def test_release_leaves_a_claim_taken_over_by_a_newer_scan(
    db_session: AsyncSession, test_user: User
):
    old_claim = await detection.claim_scan(db_session, test_user, unlimited=True)
    await db_session.execute(
        update(RecurringDetectionUsage)
        .where(RecurringDetectionUsage.owner_id == test_user.id)
        .values(claimed_at=old_claim - timedelta(minutes=11))
    )
    await db_session.commit()
    stale_claim = old_claim - timedelta(minutes=11)
    await detection.claim_scan(db_session, test_user, unlimited=True)

    # The stale scan finally fails: it must not free the newer scan's claim.
    await detection.release_scan(db_session, test_user.id, stale_claim)
    usage = await _usage(db_session, test_user)
    assert usage.status == "running"
    with pytest.raises(ConflictError):
        await detection.claim_scan(db_session, test_user, unlimited=True)


async def test_cancel_cooldown_expires(db_session: AsyncSession, test_user: User):
    claimed_at = await detection.claim_scan(db_session, test_user, unlimited=True)
    await detection._finish_scan(
        db_session, test_user.id, claimed_at=claimed_at, status=detection.CANCELLED
    )
    with pytest.raises(detection.ScanCooldownError):
        await detection.claim_scan(db_session, test_user, unlimited=True)

    await db_session.execute(
        update(RecurringDetectionUsage)
        .where(RecurringDetectionUsage.owner_id == test_user.id)
        .values(claimed_at=datetime.now(timezone.utc) - timedelta(minutes=2))
    )
    await db_session.commit()
    await detection.claim_scan(db_session, test_user, unlimited=True)


async def test_cancel_release_uses_a_session_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
):
    """The real cleanup path: a fresh session, not the cancelled request's."""
    from uuid import uuid4

    from sqlalchemy import delete

    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(detection, "async_session", TestSessionLocal)
    async with TestSessionLocal() as setup:
        user = User(email=f"cancel-{uuid4()}@example.com", country="USD")
        setup.add(user)
        await setup.commit()
        user_id = user.id
        claimed_at = await detection.claim_scan(setup, user, unlimited=False)

    try:
        detection._release_in_background(user_id, claimed_at)
        await detection.wait_for_cleanup()
        async with TestSessionLocal() as verify:
            usage = await verify.get(RecurringDetectionUsage, user_id)
            assert (usage.status, usage.scan_count) == ("cancelled", 0)
    finally:
        async with TestSessionLocal() as cleanup:
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()


async def test_scan_whose_claim_was_taken_over_raises_conflict(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    await add_series(db_session, test_user)
    real_detect = detection.detect
    newer_claim = datetime.now(timezone.utc) + timedelta(seconds=1)

    async def slow_detect(db, user, **kwargs):
        scan = await real_detect(db, user, **kwargs)
        # Meanwhile the claim went stale and a newer scan took it over.
        await db.execute(
            update(RecurringDetectionUsage)
            .where(RecurringDetectionUsage.owner_id == user.id)
            .values(claimed_at=newer_claim)
        )
        await db.commit()
        return scan

    monkeypatch.setattr(detection, "detect", slow_detect)
    with pytest.raises(ConflictError):
        await detection.run_scan(db_session, test_user)

    # Nothing was saved or counted, and the newer scan keeps its claim.
    usage = await _usage(db_session, test_user)
    assert (usage.status, usage.scan_count) == ("running", 0)
    assert usage.claimed_at == newer_claim
    assert usage.last_result is None
    assert await detection.scans_remaining(db_session, test_user) == 1


async def test_cancel_right_after_the_claim_commits_releases_it(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    import asyncio
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def test_session():
        yield db_session

    real_commit = db_session.commit

    async def commit_then_cancel():
        # The claim reached the server, then the request was cancelled.
        await real_commit()
        monkeypatch.setattr(db_session, "commit", real_commit)
        raise asyncio.CancelledError

    monkeypatch.setattr(detection, "_cleanup_session", test_session)
    monkeypatch.setattr(db_session, "commit", commit_then_cancel)
    with pytest.raises(asyncio.CancelledError):
        await detection.run_scan(db_session, test_user, unlimited=False)
    await detection.wait_for_cleanup()

    status = await detection.scan_status(db_session, test_user)
    assert status.running is False
    assert status.scans_remaining == 1
    usage = await _usage(db_session, test_user)
    assert (usage.status, usage.scan_count) == ("cancelled", 0)


async def test_newer_in_group_query_is_bounded(
    db_session: AsyncSession, test_user: User, monkeypatch: pytest.MonkeyPatch
):
    # Only the newest NEWER_IN_GROUP_LIMIT transactions are compared; the
    # newest same-merchant charge is still found.
    monkeypatch.setattr(detection, "NEWER_IN_GROUP_LIMIT", 1)
    old = await create_test_expense(
        db_session,
        owner_id=test_user.id,
        amount=30,
        expense_date=TODAY - timedelta(days=60),
        description="Gym",
    )
    for days in (40, 20):
        await create_test_expense(
            db_session,
            owner_id=test_user.id,
            amount=5,
            expense_date=TODAY - timedelta(days=days),
            description="Coffee",
        )
    newest = await create_test_expense(
        db_session,
        owner_id=test_user.id,
        amount=30,
        expense_date=TODAY - timedelta(days=1),
        description="Gym",
    )
    item = schemas.RecurringSuggestionAccept(
        fingerprint="e" * 32,
        transaction_type="expense",
        transaction_id=old.id,
        frequency="monthly",
    )
    found = await detection._newer_in_group(db_session, test_user, item, old)
    assert found is not None and found.id == newest.id


def test_ai_requests_deny_data_collection():
    from app.ai import recurring_detection as ai

    settings_ = ai.build_model().settings
    assert settings_["openrouter_provider"] == {"data_collection": "deny"}
