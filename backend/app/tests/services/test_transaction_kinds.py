"""Per-type behaviour of recurring templates and detection, and ownership statuses.

Pins what each of expense / income / transfer does when a template is created,
generated, marked from a transaction and deleted (transfers store their
accounts as `from_acc` / `to_acc`), plus the 404-vs-403 answer of every
ownership check those paths make. Structural tests at the end keep the
per-type dispatch in one registry.
"""

import ast
import inspect
import textwrap
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from app.models import Expense, Income, RecurringTransaction, Transfer, User
from app.schemas.transaction import TransactionType
from app.services import recurring as recurring_service
from app.services import recurring_detection as detection
from app.services.recurring_detection import history as detection_history
from app.services.recurring_detection import suggestions as detection_suggestions
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from tests.utils import (
    create_test_account,
    create_test_category,
    create_test_expense,
    create_test_income,
    create_test_place,
    create_test_subcategory,
    create_test_transfer,
    create_test_user,
)

PREFIX = "/api/v1/recurring"
TODAY = datetime.now(timezone.utc).date()
MODELS = {"expense": Expense, "income": Income, "transfer": Transfer}
APP_DIR = Path(recurring_service.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.transactions.invalidate_user_cache", AsyncMock(return_value=True)
    )


async def _refs(db: AsyncSession, user: User) -> dict:
    account = await create_test_account(db, owner_id=user.id)
    savings = await create_test_account(db, owner_id=user.id, name="Savings")
    category = await create_test_category(db, owner_id=user.id, name="Food")
    subcategory = await create_test_subcategory(
        db, owner_id=user.id, category_id=category.id, name="Groceries"
    )
    place = await create_test_place(db, owner_id=user.id, name="Market")
    return {
        "account_id": account.id,
        "to_account_id": savings.id,
        "category_id": category.id,
        "subcategory_id": subcategory.id,
        "place_id": place.id,
    }


async def _generated(db: AsyncSession, type_: str, recurring_id: int) -> list:
    model = MODELS[type_]
    result = await db.execute(
        select(model)
        .where(model.recurring_id == recurring_id)
        .order_by(model.date)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


def _transaction_fields(type_: str, tx) -> dict:
    if type_ == "transfer":
        return {"account_id": tx.from_acc, "to_account_id": tx.to_acc}
    fields = {
        "account_id": tx.account_id,
        "subcategory_id": tx.subcategory_id,
        "place_id": tx.place_id,
    }
    if type_ == "expense":
        fields["category_id"] = tx.category_id
    return fields


# Template reference fields each type keeps; the rest are cleared.
KEPT = {
    "expense": {"account_id", "category_id", "subcategory_id", "place_id"},
    "income": {"account_id", "subcategory_id", "place_id"},
    "transfer": {"account_id", "to_account_id"},
}
REFERENCE_FIELDS = ("account_id", "to_account_id", "category_id", "subcategory_id")
REFERENCE_FIELDS += ("place_id",)


@pytest.mark.parametrize("type_", ["expense", "income", "transfer"])
async def test_create_and_generate_per_type(
    client: AsyncClient, db_session: AsyncSession, test_user: User, type_: str
):
    refs = await _refs(db_session, test_user)
    resp = await client.post(
        PREFIX,
        json={
            "type": type_,
            "amount": 12.5,
            "description": "Plan",
            "frequency": "weekly",
            "start_date": (TODAY - timedelta(days=7)).isoformat(),
            "timezone": "UTC",
            **refs,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["type"] == type_
    for field in REFERENCE_FIELDS:
        expected = refs[field] if field in KEPT[type_] else None
        assert data[field] == expected, field

    generated = await _generated(db_session, type_, data["id"])
    assert [tx.date for tx in generated] == [TODAY - timedelta(days=7), TODAY]
    for tx in generated:
        assert (tx.amount, tx.description) == (12.5, "Plan")
        assert _transaction_fields(type_, tx) == {f: refs[f] for f in KEPT[type_]}
        if type_ != "transfer":
            assert tx.made_from == "Recurring"


@pytest.mark.parametrize("type_", ["expense", "income", "transfer"])
async def test_from_transaction_copies_fields_per_type(
    client: AsyncClient, db_session: AsyncSession, test_user: User, type_: str
):
    refs = await _refs(db_session, test_user)
    day = TODAY - timedelta(days=3)
    if type_ == "expense":
        tx = await create_test_expense(
            db_session,
            owner_id=test_user.id,
            amount=40,
            expense_date=day,
            description="Gym",
            account_id=refs["account_id"],
            category_id=refs["category_id"],
            subcategory_id=refs["subcategory_id"],
            place_id=refs["place_id"],
        )
    elif type_ == "income":
        tx = await create_test_income(
            db_session,
            owner_id=test_user.id,
            amount=40,
            income_date=day,
            description="Gym",
            account_id=refs["account_id"],
            subcategory_id=refs["subcategory_id"],
            place_id=refs["place_id"],
        )
    else:
        tx = await create_test_transfer(
            db_session,
            owner_id=test_user.id,
            from_acc=refs["account_id"],
            to_acc=refs["to_account_id"],
            amount=40,
            transfer_date=day,
            description="Gym",
        )
    resp = await client.post(
        f"{PREFIX}/from-transaction",
        json={
            "transaction_type": type_,
            "transaction_id": tx.id,
            "frequency": "monthly",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["type"] == type_
    assert (data["amount"], data["description"]) == (40, "Gym")
    assert data["start_date"] == day.isoformat()
    template = await db_session.get(
        RecurringTransaction, data["id"], populate_existing=True
    )
    assert template.source_transaction_id == tx.id
    for field in REFERENCE_FIELDS:
        expected = refs[field] if field in KEPT[type_] else None
        assert data[field] == expected, field
    linked = await db_session.get(MODELS[type_], tx.id, populate_existing=True)
    assert linked.recurring_id == data["id"]


@pytest.mark.parametrize("type_", ["expense", "income", "transfer"])
async def test_delete_generated_per_type(
    client: AsyncClient, db_session: AsyncSession, test_user: User, type_: str
):
    refs = await _refs(db_session, test_user)
    created = await client.post(
        PREFIX,
        json={
            "type": type_,
            "amount": 5,
            "frequency": "weekly",
            "start_date": (TODAY - timedelta(days=7)).isoformat(),
            **{f: refs[f] for f in KEPT[type_]},
        },
    )
    recurring_id = created.json()["id"]
    assert len(await _generated(db_session, type_, recurring_id)) == 2
    resp = await client.delete(f"{PREFIX}/{recurring_id}?delete_generated=true")
    assert resp.status_code == 200, resp.text
    assert await _generated(db_session, type_, recurring_id) == []
    assert await db_session.get(RecurringTransaction, recurring_id) is None


@pytest.mark.parametrize("type_", ["expense", "income"])
@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("account_id", "Account not found"),
        ("subcategory_id", "Subcategory not found"),
        ("place_id", "Place not found"),
        ("category_id", "Category not found"),
    ],
)
async def test_generation_pauses_on_foreign_reference(
    db_session: AsyncSession, test_user: User, type_: str, field: str, message: str
):
    if type_ == "income" and field == "category_id":
        pytest.skip("incomes have no category")
    refs = await _refs(db_session, test_user)
    template = await recurring_service.create_recurring(
        db_session,
        test_user,
        schemas.RecurringTransactionCreate(
            type=type_,
            amount=5,
            frequency="daily",
            start_date=TODAY + timedelta(days=1),
            **{f: refs[f] for f in KEPT[type_]},
        ),
    )
    other = await create_test_user(db_session, email="other@example.com")
    model = {
        "account_id": "account",
        "subcategory_id": "subcategory",
        "place_id": "place",
        "category_id": "category",
    }[field]
    from app import models

    entity = getattr(models, model.capitalize())
    await db_session.execute(
        update(entity).where(entity.id == refs[field]).values(owner_id=other.id)
    )
    await db_session.commit()
    created = await recurring_service.generate_due(
        db_session, template.id, today=TODAY + timedelta(days=1)
    )
    assert created == 0
    await db_session.refresh(template)
    assert (template.is_active, template.last_error) == (False, message)


async def test_generation_pauses_transfer_missing_or_foreign_account(
    db_session: AsyncSession, test_user: User
):
    refs = await _refs(db_session, test_user)

    async def transfer_template():
        return await recurring_service.create_recurring(
            db_session,
            test_user,
            schemas.RecurringTransactionCreate(
                type="transfer",
                amount=5,
                frequency="daily",
                start_date=TODAY + timedelta(days=1),
                account_id=refs["account_id"],
                to_account_id=refs["to_account_id"],
            ),
        )

    missing = await transfer_template()
    await db_session.execute(
        update(RecurringTransaction)
        .where(RecurringTransaction.id == missing.id)
        .values(to_account_id=None)
    )
    await db_session.commit()
    await recurring_service.generate_due(
        db_session, missing.id, today=TODAY + timedelta(days=1)
    )
    await db_session.refresh(missing)
    assert missing.last_error == "One of the transfer accounts was deleted"

    foreign = await transfer_template()
    other = await create_test_user(db_session, email="other@example.com")
    savings = await create_test_account(db_session, owner_id=other.id)
    await db_session.execute(
        update(RecurringTransaction)
        .where(RecurringTransaction.id == foreign.id)
        .values(to_account_id=savings.id)
    )
    await db_session.commit()
    await recurring_service.generate_due(
        db_session, foreign.id, today=TODAY + timedelta(days=1)
    )
    await db_session.refresh(foreign)
    # The transfer service's own check (a permission error) pauses it.
    assert (foreign.is_active, foreign.last_error) == (False, "Not enough permissions")


# ---------------------------------------------------------------------------
# load_points / accept path
# ---------------------------------------------------------------------------
async def test_load_points_maps_each_type(db_session: AsyncSession, test_user: User):
    refs = await _refs(db_session, test_user)
    day = TODAY - timedelta(days=2)
    expense = await create_test_expense(
        db_session,
        owner_id=test_user.id,
        amount=9,
        expense_date=day,
        description="Netflix",
        account_id=refs["account_id"],
        category_id=refs["category_id"],
        subcategory_id=refs["subcategory_id"],
        place_id=refs["place_id"],
    )
    income = await create_test_income(
        db_session,
        owner_id=test_user.id,
        amount=900,
        income_date=day,
        description="Salary",
        account_id=refs["account_id"],
        subcategory_id=refs["subcategory_id"],
        place_id=refs["place_id"],
    )
    transfer = await create_test_transfer(
        db_session,
        owner_id=test_user.id,
        from_acc=refs["account_id"],
        to_acc=refs["to_account_id"],
        amount=100,
        transfer_date=day,
        description="Savings",
    )
    # Not loaded: future, zero amount, already linked.
    await create_test_expense(
        db_session, owner_id=test_user.id, expense_date=TODAY + timedelta(days=1)
    )
    await create_test_expense(db_session, owner_id=test_user.id, amount=0)
    linked = await create_test_income(db_session, owner_id=test_user.id)
    template = RecurringTransaction(
        owner_id=test_user.id,
        type="income",
        amount=1,
        frequency="monthly",
        start_date=TODAY,
    )
    db_session.add(template)
    await db_session.commit()
    linked.recurring_id = template.id
    await db_session.commit()

    points = await detection.load_points(db_session, test_user, TODAY)
    by_type = {p.type.value: p for p in points}
    assert len(points) == 3
    common = {"date": day, "to_account_id": None}
    assert by_type["expense"] == detection_history.TxPoint(
        id=expense.id,
        type="expense",
        amount=9.0,
        description="Netflix",
        place_name="Market",
        account_id=refs["account_id"],
        category_id=refs["category_id"],
        subcategory_id=refs["subcategory_id"],
        place_id=refs["place_id"],
        category_name="Food",
        **common,
    )
    assert by_type["income"] == detection_history.TxPoint(
        id=income.id,
        type="income",
        amount=900.0,
        description="Salary",
        place_name="Market",
        account_id=refs["account_id"],
        subcategory_id=refs["subcategory_id"],
        place_id=refs["place_id"],
        # Incomes are labelled by their subcategory.
        category_name="Groceries",
        **common,
    )
    assert by_type["transfer"] == detection_history.TxPoint(
        id=transfer.id,
        type="transfer",
        date=day,
        amount=100.0,
        description="Savings",
        account_id=refs["account_id"],
        to_account_id=refs["to_account_id"],
    )
    assert all(isinstance(p.amount, float) for p in points)


async def test_accept_transfer_compares_accounts(
    db_session: AsyncSession, test_user: User
):
    # Outside a detected series, a newer transfer between the same accounts
    # makes the older one not the latest; one between other accounts doesn't.
    refs = await _refs(db_session, test_user)
    third = await create_test_account(db_session, owner_id=test_user.id, name="Third")
    old = await create_test_transfer(
        db_session,
        owner_id=test_user.id,
        from_acc=refs["account_id"],
        to_acc=refs["to_account_id"],
        amount=100,
        transfer_date=TODAY - timedelta(days=60),
        description="Savings",
    )
    await create_test_transfer(
        db_session,
        owner_id=test_user.id,
        from_acc=refs["account_id"],
        to_acc=third.id,
        amount=100,
        transfer_date=TODAY - timedelta(days=30),
        description="Savings",
    )
    item = schemas.RecurringSuggestionAccept(
        fingerprint="a" * 32,
        transaction_type="transfer",
        transaction_id=old.id,
        frequency="monthly",
    )
    assert (
        await detection_suggestions._newer_in_group(db_session, test_user, item, old)
        is None
    )
    newer = await create_test_transfer(
        db_session,
        owner_id=test_user.id,
        from_acc=refs["account_id"],
        to_acc=refs["to_account_id"],
        amount=100,
        transfer_date=TODAY - timedelta(days=1),
        description="Savings",
    )
    found = await detection_suggestions._newer_in_group(
        db_session, test_user, item, old
    )
    assert found is not None
    assert (found.id, found.account_id, found.to_account_id) == (
        newer.id,
        refs["account_id"],
        refs["to_account_id"],
    )


async def test_accept_foreign_or_missing_transaction(
    db_session: AsyncSession, test_user: User
):
    other = await create_test_user(db_session, email="other@example.com")
    foreign = await create_test_expense(db_session, owner_id=other.id)
    items = [
        schemas.RecurringSuggestionAccept(
            fingerprint=char * 32,
            transaction_type="expense",
            transaction_id=tx_id,
            frequency="monthly",
        )
        for char, tx_id in (("b", foreign.id), ("c", 999999))
    ]
    results = await detection.accept_suggestions(db_session, test_user, items)
    assert [(r.status, r.error) for r in results] == [
        ("error", "Transaction not found"),
        ("error", "Transaction not found"),
    ]


# ---------------------------------------------------------------------------
# Ownership statuses (404 hides another user's ids; 403 where it is reported)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "field", ["account_id", "category_id", "subcategory_id", "place_id"]
)
async def test_recurring_create_hides_foreign_references(
    client: AsyncClient, db_session: AsyncSession, field: str
):
    other = await create_test_user(db_session, email="other@example.com")
    foreign = await _refs(db_session, other)
    body = {
        "type": "expense",
        "amount": 5,
        "frequency": "monthly",
        "start_date": TODAY.isoformat(),
        field: foreign[field],
    }
    resp = await client.post(PREFIX, json=body)
    assert resp.status_code == 404, resp.text
    body[field] = 999999
    assert (await client.post(PREFIX, json=body)).status_code == 404


async def test_recurring_transfer_hides_foreign_to_account(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    other = await create_test_user(db_session, email="other@example.com")
    mine = await create_test_account(db_session, owner_id=test_user.id)
    theirs = await create_test_account(db_session, owner_id=other.id)
    resp = await client.post(
        PREFIX,
        json={
            "type": "transfer",
            "amount": 5,
            "frequency": "monthly",
            "start_date": TODAY.isoformat(),
            "account_id": mine.id,
            "to_account_id": theirs.id,
        },
    )
    assert resp.status_code == 404


async def test_recurring_update_hides_foreign_reference(
    client: AsyncClient, db_session: AsyncSession
):
    created = await client.post(
        PREFIX,
        json={
            "type": "expense",
            "amount": 5,
            "frequency": "monthly",
            "start_date": (TODAY + timedelta(days=1)).isoformat(),
        },
    )
    other = await create_test_user(db_session, email="other@example.com")
    theirs = await create_test_account(db_session, owner_id=other.id)
    resp = await client.put(
        f"{PREFIX}/{created.json()['id']}", json={"account_id": theirs.id}
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("type_", ["expense", "income", "transfer"])
async def test_from_transaction_hides_foreign_and_missing(
    client: AsyncClient,
    superuser_client: AsyncClient,
    db_session: AsyncSession,
    type_: str,
):
    other = await create_test_user(db_session, email="other@example.com")
    if type_ == "expense":
        tx = await create_test_expense(db_session, owner_id=other.id)
    elif type_ == "income":
        tx = await create_test_income(db_session, owner_id=other.id)
    else:
        a = await create_test_account(db_session, owner_id=other.id)
        b = await create_test_account(db_session, owner_id=other.id, name="B")
        tx = await create_test_transfer(
            db_session, owner_id=other.id, from_acc=a.id, to_acc=b.id
        )
    url = f"{PREFIX}/from-transaction"

    def body(tx_id: int) -> dict:
        return {
            "transaction_type": type_,
            "transaction_id": tx_id,
            "frequency": "monthly",
        }

    assert (await client.post(url, json=body(tx.id))).status_code == 404
    assert (await client.post(url, json=body(999999))).status_code == 404
    # Even a superuser can't mark someone else's transaction.
    assert (await superuser_client.post(url, json=body(tx.id))).status_code == 404


@pytest.mark.parametrize("path", ["expenses", "incomes", "transfers"])
async def test_transaction_reads_report_foreign_as_forbidden(
    client: AsyncClient, db_session: AsyncSession, path: str
):
    other = await create_test_user(db_session, email="other@example.com")
    if path == "expenses":
        tx = await create_test_expense(db_session, owner_id=other.id)
    elif path == "incomes":
        tx = await create_test_income(db_session, owner_id=other.id)
    else:
        a = await create_test_account(db_session, owner_id=other.id)
        b = await create_test_account(db_session, owner_id=other.id, name="B")
        tx = await create_test_transfer(
            db_session, owner_id=other.id, from_acc=a.id, to_acc=b.id
        )
    assert (await client.get(f"/api/v1/{path}/{tx.id}")).status_code == 403
    assert (await client.get(f"/api/v1/{path}/999999")).status_code == 404
    assert (await client.delete(f"/api/v1/{path}/{tx.id}")).status_code == 403


async def test_transfer_create_reports_foreign_account_as_forbidden(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    other = await create_test_user(db_session, email="other@example.com")
    mine = await create_test_account(db_session, owner_id=test_user.id)
    theirs = await create_test_account(db_session, owner_id=other.id)
    body = {
        "amount": 5,
        "date": TODAY.isoformat(),
        "from_acc": mine.id,
        "to_acc": theirs.id,
    }
    assert (await client.post("/api/v1/transfers", json=body)).status_code == 403
    body["to_acc"] = 999999
    assert (await client.post("/api/v1/transfers", json=body)).status_code == 404


async def test_expense_create_and_update_drop_foreign_references(
    client: AsyncClient, db_session: AsyncSession, test_user: User
):
    other = await create_test_user(db_session, email="other@example.com")
    theirs = await _refs(db_session, other)
    mine = await _refs(db_session, test_user)
    fields = ("account_id", "category_id", "subcategory_id", "place_id")
    resp = await client.post(
        "/api/v1/expenses",
        json={"amount": 5, "date": TODAY.isoformat(), **{f: theirs[f] for f in fields}},
    )
    assert resp.status_code == 200, resp.text
    expense_id = resp.json()["id"]
    expense = await db_session.get(Expense, expense_id, populate_existing=True)
    assert [getattr(expense, f) for f in fields] == [None] * 4

    await db_session.execute(
        update(Expense)
        .where(Expense.id == expense_id)
        .values(**{f: mine[f] for f in fields})
    )
    await db_session.commit()
    resp = await client.put(
        f"/api/v1/expenses/{expense_id}", json={f: theirs[f] for f in fields}
    )
    assert resp.status_code == 200, resp.text
    expense = await db_session.get(Expense, expense_id, populate_existing=True)
    assert [getattr(expense, f) for f in fields] == [mine[f] for f in fields]


# ---------------------------------------------------------------------------
# Structure: one registry for the per-type dispatch, one ownership helper
# ---------------------------------------------------------------------------
# services/recurring.py and the modules split out of it.
RECURRING_SOURCES = (
    "services/recurring.py",
    "services/recurring_fields.py",
    "services/recurring_generation.py",
    "services/recurring_leases.py",
    "services/recurring_schedule.py",
)


def _source(relative: str) -> str:
    return (APP_DIR / relative).read_text()


def test_load_points_reads_rows_by_name():
    tree = ast.parse(textwrap.dedent(inspect.getsource(detection.load_points)))
    positional = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "row"
    ]
    assert positional == []
    # One query in a loop over the registry, not one per type.
    assert "models.Expense" not in inspect.getsource(detection.load_points)


def test_single_type_registry():
    from app.services import transaction_kinds

    assert set(transaction_kinds.KINDS) == set(TransactionType)
    detection_paths = sorted(
        (APP_DIR / "services" / "recurring_detection").glob("*.py")
    )
    assert detection_paths
    detection_modules = [str(path.relative_to(APP_DIR)) for path in detection_paths]
    for path in detection_paths:
        name = detection.__name__
        if path.stem != "__init__":
            name = f"{name}.{path.stem}"
        assert not hasattr(import_module(name), "_TYPE_MODELS")
    assert not hasattr(recurring_service, "TRANSACTION_MODELS")
    for relative in (*RECURRING_SOURCES, *detection_modules):
        source = _source(relative)
        for name in ("models.Expense", "delete_expense", "get_expense", "from_acc"):
            assert name not in source, (relative, name)


def test_no_recurring_type_conversions():
    from app.core import recurrence

    # Templates use TransactionType directly; the old alias is gone.
    assert not hasattr(recurrence, "Recurring" + "Type")
    for path in APP_DIR.rglob("*.py"):
        if "tests" in path.parts:
            continue
        assert "Recurring" + "Type(" not in path.read_text(), path


def test_single_ownership_helper():
    recurring_source = "".join(_source(relative) for relative in RECURRING_SOURCES)
    transactions_source = _source("services/transactions.py")
    assert "_require_ref" not in recurring_source
    assert "crud_obj.get" not in recurring_source
    assert "PermissionDeniedError" not in recurring_source
    assert "_require_owned" not in transactions_source
    assert "_ensure_owned_optional" not in transactions_source
    assert "owner_id != user.id" not in transactions_source
    assert "ownership" in recurring_source and "ownership" in transactions_source
