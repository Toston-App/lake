"""Import sessions: upload -> map columns -> match values -> commit -> undo."""

from __future__ import annotations

import hashlib
import json
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, delete, exists, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from app import schemas
from app.models.account import Account
from app.models.balance_adjustment import BalanceAdjustment
from app.models.category import Category
from app.models.expense import Expense
from app.models.holding import Holding
from app.models.imports import Import, ImportProfile, ImportService, ImportStatus
from app.models.income import Income
from app.models.investment_transaction import InvestmentTransaction
from app.models.place import Place
from app.models.subcategory import Subcategory
from app.models.transfer import Transfer
from app.models.user import User
from app.services.imports import matching
from app.services.imports.parsing import (
    ColumnMapping,
    ParsedRow,
    ParseOptions,
    ParseResult,
    detect_category_separator,
    detect_date_format,
    detect_decimal_separator,
    detect_mapping,
    header_fingerprint,
    parse_rows,
    validate_mapping,
)
from app.services.imports.reader import UnsafeFileError, clean_cell, read_upload
from app.utilities.encryption import get_fernet_key

DRAFT_TTL = timedelta(hours=2)
SAMPLE_ROWS = 20
PREVIEW_ROWS = 50
MAX_ERRORS_RETURNED = 200
MADE_FROM = "Import"
DEFAULT_ACCOUNT_COLOR = "#168FFF"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Encrypted staging of parsed rows
# ---------------------------------------------------------------------------
def encode_payload(payload: dict[str, Any]) -> bytes:
    raw = zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 6)
    return get_fernet_key().encrypt(raw)


def decode_payload(blob: bytes) -> dict[str, Any]:
    return json.loads(zlib.decompress(get_fernet_key().decrypt(bytes(blob))))


# ---------------------------------------------------------------------------
# Loading the user's data
# ---------------------------------------------------------------------------
async def _load_accounts(db: AsyncSession, owner_id: int) -> list[matching.NamedEntity]:
    result = await db.execute(
        select(Account.id, Account.name).where(Account.owner_id == owner_id)
    )
    return [matching.NamedEntity(id=r.id, name=r.name or "") for r in result]


async def _load_places(db: AsyncSession, owner_id: int) -> list[matching.NamedEntity]:
    result = await db.execute(
        select(Place.id, Place.name).where(Place.owner_id == owner_id)
    )
    return [matching.NamedEntity(id=r.id, name=r.name or "") for r in result]


async def _load_categories(
    db: AsyncSession, owner_id: int
) -> list[matching.CategoryEntity]:
    result = await db.execute(
        select(Category)
        .where(Category.owner_id == owner_id)
        .options(selectinload(Category.subcategories))
        .order_by(Category.name)
    )
    return [
        matching.CategoryEntity(
            id=c.id,
            name=c.name,
            is_income=bool(c.is_income),
            subcategories=[
                matching.SubcategoryEntity(id=s.id, name=s.name, category_id=c.id)
                for s in sorted(c.subcategories, key=lambda s: s.name)
            ],
        )
        for c in result.scalars().all()
    ]


async def _get_profile(
    db: AsyncSession, owner_id: int, fingerprint: str
) -> ImportProfile | None:
    result = await db.execute(
        select(ImportProfile).where(
            ImportProfile.owner_id == owner_id,
            ImportProfile.header_fingerprint == fingerprint,
        )
    )
    return result.scalars().first()


async def get_draft(db: AsyncSession, owner_id: int, import_id: int) -> Import:
    result = await db.execute(
        select(Import).where(
            Import.id == import_id,
            Import.owner_id == owner_id,
            Import.status == ImportStatus.draft,
        )
    )
    draft = result.scalars().first()
    if draft is None or draft.staged_payload is None:
        raise HTTPException(status_code=404, detail="Import session not found")
    if draft.expires_at and draft.expires_at < _now():
        raise HTTPException(
            status_code=410, detail="Import session expired, upload the file again"
        )
    return draft


# ---------------------------------------------------------------------------
# 1. Upload
# ---------------------------------------------------------------------------
def _headers_and_grid(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    width = max(len(r) for r in rows)
    grid = [r + [""] * (width - len(r)) for r in rows]
    headers = [h or f"Column {i + 1}" for i, h in enumerate(grid[0])]
    grid[0] = headers
    return headers, grid


def _detect_options(grid: list[list[str]], mapping: ColumnMapping) -> ParseOptions:
    body = grid[1:]

    def column(index: int | None) -> list[str]:
        if index is None:
            return []
        return [r[index] for r in body if index < len(r) and r[index]]

    amounts = column(mapping.amount) + column(mapping.debit) + column(mapping.credit)
    return ParseOptions(
        date_format=detect_date_format(column(mapping.date)),  # type: ignore[arg-type]
        decimal_separator=detect_decimal_separator(amounts),  # type: ignore[arg-type]
        category_separator=(
            detect_category_separator(column(mapping.category))  # type: ignore[arg-type]
            if mapping.subcategory is None
            else None
        ),
    )


async def purge_expired_drafts(db: AsyncSession) -> None:
    await db.execute(
        update(Import)
        .where(Import.status == ImportStatus.draft, Import.expires_at < _now())
        .values(status=ImportStatus.failed, staged_payload=None)
    )


async def create_session(
    db: AsyncSession, user: User, filename: str, data: bytes
) -> schemas.SessionCreated:
    try:
        read = await run_in_threadpool(read_upload, filename, data)
    except UnsafeFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    headers, grid = _headers_and_grid(read.rows)
    fingerprint = header_fingerprint(headers)
    file_hash = hashlib.sha256(data).hexdigest()

    profile = await _get_profile(db, user.id, fingerprint)
    mapping = detect_mapping(headers)
    options = _detect_options(grid, mapping)
    if profile is not None:
        saved_mapping = ColumnMapping(**profile.column_mapping)
        if not validate_mapping(saved_mapping, len(headers)):
            mapping = saved_mapping
            options = ParseOptions(**profile.options)
            if options.default_account_id is not None:
                owned = await db.execute(
                    select(Account.id).where(
                        Account.id == options.default_account_id,
                        Account.owner_id == user.id,
                    )
                )
                if owned.scalar() is None:
                    options.default_account_id = None
        profile.last_used_at = _now()

    previous = await db.execute(
        select(func.max(Import.ended_at)).where(
            Import.owner_id == user.id,
            Import.file_hash == file_hash,
            Import.status == ImportStatus.completed,
        )
    )
    previously_imported_at = previous.scalar()

    # One active session per user; also clean up anybody's expired drafts.
    await db.execute(
        update(Import)
        .where(Import.owner_id == user.id, Import.status == ImportStatus.draft)
        .values(status=ImportStatus.failed, staged_payload=None)
    )
    await purge_expired_drafts(db)

    expires_at = _now() + DRAFT_TTL
    draft = Import(
        owner_id=user.id,
        service=ImportService.CSV,
        status=ImportStatus.draft,
        filename=clean_cell(filename)[:255] or "import.csv",
        file_size=len(data),
        file_hash=file_hash,
        header_fingerprint=fingerprint,
        mapping={
            "columns": mapping.model_dump(),
            "options": options.model_dump(),
            "profile_id": profile.id if profile else None,
            "file_type": read.file_type,
        },
        staged_payload=encode_payload({"grid": grid}),
        expires_at=expires_at,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)

    return schemas.SessionCreated(
        id=draft.id,
        filename=draft.filename,
        file_type=read.file_type,  # type: ignore[arg-type]
        headers=headers,
        sample_rows=grid[1 : SAMPLE_ROWS + 1],
        total_rows=len(grid) - 1,
        mapping=mapping,
        options=options,
        profile_id=profile.id if profile else None,
        profile_name=profile.name if profile else None,
        previously_imported_at=previously_imported_at,
        expires_at=expires_at,
        truncated_cells=read.truncated_cells,
    )


async def cancel_session(db: AsyncSession, user: User, import_id: int) -> None:
    draft = await get_draft(db, user.id, import_id)
    draft.status = ImportStatus.failed
    draft.staged_payload = None
    await db.commit()


# ---------------------------------------------------------------------------
# 2. Column mapping -> values to match
# ---------------------------------------------------------------------------
async def _parse_draft(
    draft: Import, mapping: ColumnMapping, options: ParseOptions
) -> ParseResult:
    grid = decode_payload(draft.staged_payload)["grid"]
    problems = validate_mapping(mapping, len(grid[0]))
    if options.skip_rows >= len(grid) - 1:
        problems.append("Too many rows skipped.")
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))
    return await run_in_threadpool(parse_rows, grid, mapping, options)


async def _check_default_account(
    db: AsyncSession, owner_id: int, options: ParseOptions
) -> None:
    if options.default_account_id is None:
        return
    found = await db.execute(
        select(Account.id).where(
            Account.id == options.default_account_id, Account.owner_id == owner_id
        )
    )
    if found.scalar() is None:
        raise HTTPException(status_code=422, detail="Default account not found")


def _saved_values(profile: ImportProfile | None) -> dict[str, dict[str, Any]]:
    if profile is None or not profile.value_mappings:
        return {}
    return profile.value_mappings


async def apply_mapping(
    db: AsyncSession, user: User, draft: Import, body: schemas.MappingIn
) -> schemas.MappingResult:
    await _check_default_account(db, user.id, body.options)
    parsed = await _parse_draft(draft, body.mapping, body.options)

    profile = await _get_profile(db, user.id, draft.header_fingerprint)
    saved = _saved_values(profile)
    accounts = await _load_accounts(db, user.id)
    places = await _load_places(db, user.id)
    categories = await _load_categories(db, user.id)

    account_entries = [
        schemas.ValueEntry(
            **item,
            suggestion=matching.suggest_named(
                item["value"], accounts, saved.get("accounts")
            ),
        )
        for item in matching.group_values(matching.collect_accounts(parsed.rows))
    ]
    place_entries = [
        schemas.ValueEntry(
            **item,
            suggestion=matching.suggest_named(
                item["value"], places, saved.get("places")
            ),
        )
        for item in matching.group_values(matching.collect_places(parsed.rows))
    ]
    category_entries = [
        schemas.CategoryEntry(
            **item,
            suggestion=matching.suggest_category(
                item["kind"],
                item["category"],
                item["subcategory"],
                categories,
                saved.get("categories"),
            ),
        )
        for item in matching.collect_categories(parsed.rows)
    ]

    draft.mapping = {
        **(draft.mapping or {}),
        "columns": body.mapping.model_dump(),
        "options": body.options.model_dump(),
        "mapped": True,
    }
    draft.expires_at = _now() + DRAFT_TTL
    await db.commit()

    dates = [r.date for r in parsed.rows]
    without_account = sum(
        1
        for r in parsed.rows
        if not r.account and body.options.default_account_id is None
    )
    return schemas.MappingResult(
        summary=schemas.MappingSummary(
            total_rows=parsed.total_rows,
            valid_rows=sum(len(r.rows) for r in parsed.rows),
            error_rows=len(parsed.errors),
            ignored_rows=parsed.ignored_rows,
            expenses=parsed.counts.get("expense", 0),
            incomes=parsed.counts.get("income", 0),
            transfers=parsed.counts.get("transfer", 0),
            date_format=parsed.date_format,
            first_date=min(dates).isoformat() if dates else None,
            last_date=max(dates).isoformat() if dates else None,
            rows_without_account=without_account,
        ),
        errors=[
            schemas.RowErrorOut(row=e.row, message=e.message)
            for e in parsed.errors[:MAX_ERRORS_RETURNED]
        ],
        accounts=account_entries,
        places=place_entries,
        categories=category_entries,
        preview=[_preview_row(r) for r in parsed.rows[:PREVIEW_ROWS]],
    )


def _preview_row(row: ParsedRow) -> schemas.PreviewRow:
    return schemas.PreviewRow(
        rows=row.rows,
        kind=row.kind,
        date=row.date.isoformat(),
        amount=float(row.amount),
        description=row.full_description,
        account=row.account,
        to_account=row.to_account,
        category=row.category,
        subcategory=row.subcategory,
        place=row.place,
    )


# ---------------------------------------------------------------------------
# 3. Decisions -> plan
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ref:
    """Either an existing id or a placeholder for an entity to create."""

    existing: int | None = None
    new: str | None = None


@dataclass
class Plan:
    parsed: ParseResult
    options: ParseOptions
    accounts: dict[str, Ref | None]
    places: dict[str, Ref | None]
    # key -> (category ref, subcategory ref)
    categories: dict[str, tuple[Ref | None, Ref | None]]
    new_accounts: dict[str, schemas.CreateAccountDecision] = field(default_factory=dict)
    new_places: dict[str, schemas.CreatePlaceDecision] = field(default_factory=dict)
    # placeholder -> (name, color, icon, is_income)
    new_categories: dict[str, tuple[str, str, str | None, bool]] = field(
        default_factory=dict
    )
    # placeholder -> (category ref, name)
    new_subcategories: dict[str, tuple[Ref, str]] = field(default_factory=dict)


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


async def _build_plan(
    db: AsyncSession, user: User, draft: Import, decisions: schemas.Decisions
) -> Plan:
    stored = draft.mapping or {}
    if not stored.get("mapped"):
        raise _bad("Confirm the column mapping first")
    mapping = ColumnMapping(**stored["columns"])
    options = ParseOptions(**stored["options"])
    await _check_default_account(db, user.id, options)
    parsed = await _parse_draft(draft, mapping, options)

    account_ids = {a.id for a in await _load_accounts(db, user.id)}
    place_ids = {p.id for p in await _load_places(db, user.id)}
    categories = {c.id: c for c in await _load_categories(db, user.id)}

    plan = Plan(parsed=parsed, options=options, accounts={}, places={}, categories={})

    # Accounts -------------------------------------------------------------
    created_by_name: dict[str, str] = {}
    for item in matching.group_values(matching.collect_accounts(parsed.rows)):
        key = item["key"]
        decision = decisions.accounts.get(key)
        if decision is None or decision.action == "none":
            plan.accounts[key] = None
        elif decision.action == "match":
            if decision.id not in account_ids:
                raise _bad(f"Account for '{item['value']}' not found")
            plan.accounts[key] = Ref(existing=decision.id)
        else:
            name_key = matching.value_key(decision.name)
            placeholder = created_by_name.setdefault(name_key, f"account:{name_key}")
            plan.new_accounts.setdefault(placeholder, decision)
            plan.accounts[key] = Ref(new=placeholder)

    # Places ---------------------------------------------------------------
    created_by_name = {}
    for item in matching.group_values(matching.collect_places(parsed.rows)):
        key = item["key"]
        decision = decisions.places.get(key)
        if decision is None or decision.action == "none":
            plan.places[key] = None
        elif decision.action == "match":
            if decision.id not in place_ids:
                raise _bad(f"Place for '{item['value']}' not found")
            plan.places[key] = Ref(existing=decision.id)
        else:
            name_key = matching.value_key(decision.name)
            placeholder = created_by_name.setdefault(name_key, f"place:{name_key}")
            plan.new_places.setdefault(placeholder, decision)
            plan.places[key] = Ref(new=placeholder)

    # Categories -----------------------------------------------------------
    for item in matching.collect_categories(parsed.rows):
        key, kind = item["key"], item["kind"]
        label = " > ".join(v for v in (item["category"], item["subcategory"]) if v)
        is_income = kind == "income"
        decision = decisions.categories.get(key)
        if decision is None or decision.action == "none":
            plan.categories[key] = (None, None)
            continue

        if decision.action == "match":
            category = categories.get(decision.category_id)
            if category is None:
                raise _bad(f"Category for '{label}' not found")
            if category.is_income != is_income:
                raise _bad(
                    f"'{label}' is an {kind} but the selected category is not for {kind}s"
                )
            sub_ref = None
            if decision.subcategory_id is not None:
                if not any(
                    s.id == decision.subcategory_id for s in category.subcategories
                ):
                    raise _bad(f"Subcategory for '{label}' not found in that category")
                sub_ref = Ref(existing=decision.subcategory_id)
            elif is_income:
                raise _bad(f"Incomes need a subcategory: pick one for '{label}'")
            plan.categories[key] = (Ref(existing=category.id), sub_ref)

        elif decision.action == "create_subcategory":
            category = categories.get(decision.category_id)
            if category is None:
                raise _bad(f"Category for '{label}' not found")
            if category.is_income != is_income:
                raise _bad(
                    f"'{label}' is an {kind} but the selected category is not for {kind}s"
                )
            parent = Ref(existing=category.id)
            sub_ref = _new_subcategory(plan, parent, decision.name)
            plan.categories[key] = (parent, sub_ref)

        else:  # create_category
            cat_placeholder = (
                f"category:{int(is_income)}:{matching.value_key(decision.name)}"
            )
            plan.new_categories.setdefault(
                cat_placeholder,
                (decision.name, decision.color, decision.icon, is_income),
            )
            parent = Ref(new=cat_placeholder)
            sub_name = decision.subcategory_name or (
                decision.name if is_income else None
            )
            sub_ref = _new_subcategory(plan, parent, sub_name) if sub_name else None
            plan.categories[key] = (parent, sub_ref)

    return plan


def _new_subcategory(plan: Plan, parent: Ref, name: str) -> Ref:
    parent_id = parent.existing if parent.existing is not None else parent.new
    placeholder = f"subcategory:{parent_id}:{matching.value_key(name)}"
    plan.new_subcategories.setdefault(placeholder, (parent, name))
    return Ref(new=placeholder)


# ---------------------------------------------------------------------------
# 4. Plan -> transactions
# ---------------------------------------------------------------------------
@dataclass
class Txn:
    row: ParsedRow
    account: Ref | None
    to_account: Ref | None = None
    category: Ref | None = None
    subcategory: Ref | None = None
    place: Ref | None = None


def _transactions(plan: Plan) -> tuple[list[Txn], list[schemas.RowErrorOut]]:
    default = (
        Ref(existing=plan.options.default_account_id)
        if plan.options.default_account_id is not None
        else None
    )
    txns: list[Txn] = []
    problems: list[schemas.RowErrorOut] = []
    for row in plan.parsed.rows:
        account = (
            plan.accounts.get(matching.value_key(row.account))
            if row.account
            else default
        )
        if row.kind == "transfer":
            to_account = plan.accounts.get(matching.value_key(row.to_account))
            if account is None or to_account is None:
                problems.append(
                    schemas.RowErrorOut(
                        row=row.rows[0],
                        message="Transfers need both accounts; this row was skipped.",
                    )
                )
                continue
            if account == to_account:
                problems.append(
                    schemas.RowErrorOut(
                        row=row.rows[0],
                        message="Transfer between the same account; this row was skipped.",
                    )
                )
                continue
            txns.append(Txn(row=row, account=account, to_account=to_account))
            continue

        category_ref, sub_ref = (None, None)
        if row.category or row.subcategory:
            category_ref, sub_ref = plan.categories.get(
                matching.category_key(row.kind, row.category, row.subcategory),
                (None, None),
            )
        place = plan.places.get(matching.value_key(row.place)) if row.place else None
        txns.append(
            Txn(
                row=row,
                account=account,
                category=category_ref,
                subcategory=sub_ref,
                place=place,
            )
        )
    return txns, problems


def _cents(amount: Decimal | float) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))


async def _existing_counters(
    db: AsyncSession, owner_id: int, start: date, end: date
) -> Counter:
    counter: Counter = Counter()
    for model, kind in ((Expense, "expense"), (Income, "income")):
        result = await db.execute(
            select(model.date, model.amount, model.account_id).where(
                model.owner_id == owner_id, model.date >= start, model.date <= end
            )
        )
        for r in result:
            counter[(kind, r.date, _cents(r.amount), r.account_id)] += 1
    result = await db.execute(
        select(
            Transfer.date, Transfer.amount, Transfer.from_acc, Transfer.to_acc
        ).where(
            Transfer.owner_id == owner_id, Transfer.date >= start, Transfer.date <= end
        )
    )
    for r in result:
        counter[("transfer", r.date, _cents(r.amount), (r.from_acc, r.to_acc))] += 1
    return counter


def _dup_key(txn: Txn) -> tuple | None:
    """Duplicates can only exist when every account involved already exists."""
    account = txn.account.existing if txn.account else None
    if txn.account is not None and account is None:
        return None
    if txn.row.kind == "transfer":
        to = txn.to_account.existing if txn.to_account else None
        if to is None:
            return None
        return ("transfer", txn.row.date, _cents(txn.row.amount), (account, to))
    return (txn.row.kind, txn.row.date, _cents(txn.row.amount), account)


async def _split_duplicates(
    db: AsyncSession, owner_id: int, txns: list[Txn]
) -> tuple[list[Txn], list[Txn]]:
    if not txns:
        return [], []
    start = min(t.row.date for t in txns)
    end = max(t.row.date for t in txns)
    existing = await _existing_counters(db, owner_id, start, end)
    fresh, duplicates = [], []
    for txn in txns:
        key = _dup_key(txn)
        if key is not None and existing[key] > 0:
            existing[key] -= 1
            duplicates.append(txn)
        else:
            fresh.append(txn)
    return fresh, duplicates


def _uncategorized(txn: Txn) -> bool:
    return (
        txn.row.kind != "transfer"
        and bool(txn.row.category or txn.row.subcategory)
        and txn.category is None
    )


async def preview_commit(
    db: AsyncSession, user: User, draft: Import, decisions: schemas.Decisions
) -> schemas.CommitPreview:
    plan = await _build_plan(db, user, draft, decisions)
    txns, problems = _transactions(plan)
    fresh, duplicates = await _split_duplicates(db, user.id, txns)
    selected = fresh if decisions.skip_duplicates else txns
    kinds = Counter(t.row.kind for t in selected)
    return schemas.CommitPreview(
        expenses=kinds.get("expense", 0),
        incomes=kinds.get("income", 0),
        transfers=kinds.get("transfer", 0),
        duplicates=len(duplicates),
        skipped=len(problems) + len(plan.parsed.errors),
        accounts_to_create=len(plan.new_accounts),
        places_to_create=len(plan.new_places),
        categories_to_create=len(plan.new_categories),
        subcategories_to_create=len(plan.new_subcategories),
        problems=(
            [
                schemas.RowErrorOut(row=e.row, message=e.message)
                for e in plan.parsed.errors
            ]
            + problems
        )[:MAX_ERRORS_RETURNED],
    )


# ---------------------------------------------------------------------------
# 5. Commit
# ---------------------------------------------------------------------------
@dataclass
class Deltas:
    account: dict[int, dict[str, float]] = field(
        default_factory=lambda: defaultdict(
            lambda: {"exp": 0.0, "inc": 0.0, "tin": 0.0, "tout": 0.0}
        )
    )
    category: dict[int, float] = field(default_factory=lambda: defaultdict(float))
    subcategory: dict[int, float] = field(default_factory=lambda: defaultdict(float))
    expenses: float = 0.0
    incomes: float = 0.0


async def apply_deltas(
    db: AsyncSession, owner_id: int, deltas: Deltas, sign: int = 1
) -> None:
    """Add (sign=1) or remove (sign=-1) imported amounts from every running total."""
    for account_id, d in deltas.account.items():
        exp, inc = sign * d["exp"], sign * d["inc"]
        tin, tout = sign * d["tin"], sign * d["tout"]
        await db.execute(
            update(Account)
            .where(Account.id == account_id, Account.owner_id == owner_id)
            .values(
                total_expenses=func.coalesce(Account.total_expenses, 0) + exp,
                total_incomes=func.coalesce(Account.total_incomes, 0) + inc,
                total_transfers_in=func.coalesce(Account.total_transfers_in, 0) + tin,
                total_transfers_out=func.coalesce(Account.total_transfers_out, 0)
                + tout,
                current_balance=func.coalesce(Account.current_balance, 0)
                - exp
                + inc
                + tin
                - tout,
            )
            .execution_options(synchronize_session=False)
        )
    for category_id, amount in deltas.category.items():
        await db.execute(
            update(Category)
            .where(Category.id == category_id, Category.owner_id == owner_id)
            .values(total=Category.total + sign * amount)
            .execution_options(synchronize_session=False)
        )
    for subcategory_id, amount in deltas.subcategory.items():
        await db.execute(
            update(Subcategory)
            .where(Subcategory.id == subcategory_id, Subcategory.owner_id == owner_id)
            .values(total=Subcategory.total + sign * amount)
            .execution_options(synchronize_session=False)
        )
    exp, inc = sign * deltas.expenses, sign * deltas.incomes
    if exp or inc:
        await db.execute(
            update(User)
            .where(User.id == owner_id)
            .values(
                balance_total=func.coalesce(User.balance_total, 0) + inc - exp,
                balance_income=func.coalesce(User.balance_income, 0) + inc,
                balance_outcome=func.coalesce(User.balance_outcome, 0) + exp,
            )
            .execution_options(synchronize_session=False)
        )


async def _create_entities(
    db: AsyncSession, user: User, plan: Plan, import_id: int
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for placeholder, decision in plan.new_accounts.items():
        account = Account(
            name=decision.name,
            type=decision.type,
            color=decision.color or DEFAULT_ACCOUNT_COLOR,
            initial_balance=0.0,
            current_balance=0.0,
            owner_id=user.id,
            import_id=import_id,
        )
        db.add(account)
        await db.flush()
        ids[placeholder] = account.id
    for placeholder, decision in plan.new_places.items():
        place = Place(
            name=decision.name,
            is_online=decision.is_online,
            owner_id=user.id,
            import_id=import_id,
        )
        db.add(place)
        await db.flush()
        ids[placeholder] = place.id
    for placeholder, (name, color, icon, is_income) in plan.new_categories.items():
        category = Category(
            name=name,
            color=color,
            icon=icon,
            is_income=is_income,
            is_default=False,
            total=0.0,
            owner_id=user.id,
            import_id=import_id,
        )
        db.add(category)
        await db.flush()
        ids[placeholder] = category.id
    for placeholder, (parent, name) in plan.new_subcategories.items():
        parent_id = parent.existing if parent.existing is not None else ids[parent.new]
        subcategory = Subcategory(
            name=name,
            category_id=parent_id,
            is_default=False,
            total=0.0,
            owner_id=user.id,
            import_id=import_id,
        )
        db.add(subcategory)
        await db.flush()
        ids[placeholder] = subcategory.id
    return ids


def _resolve(ref: Ref | None, ids: dict[str, int]) -> int | None:
    if ref is None:
        return None
    return ref.existing if ref.existing is not None else ids[ref.new]


def _value_mappings(
    plan: Plan, ids: dict[str, int], previous: dict[str, Any]
) -> dict[str, Any]:
    """Remember the final ids so the next file with these headers maps itself."""
    result: dict[str, dict[str, Any]] = {
        "accounts": dict(previous.get("accounts", {})),
        "places": dict(previous.get("places", {})),
        "categories": dict(previous.get("categories", {})),
    }
    for section, refs in (("accounts", plan.accounts), ("places", plan.places)):
        for key, ref in refs.items():
            if ref is None:
                result[section].pop(key, None)
            else:
                result[section][key] = {"action": "match", "id": _resolve(ref, ids)}
    for key, (cat_ref, sub_ref) in plan.categories.items():
        if cat_ref is None:
            result["categories"].pop(key, None)
        else:
            result["categories"][key] = {
                "action": "match",
                "category_id": _resolve(cat_ref, ids),
                "subcategory_id": _resolve(sub_ref, ids),
            }
    return result


async def commit_import(
    db: AsyncSession, user: User, import_id: int, decisions: schemas.Decisions
) -> schemas.CommitResult:
    # Lock the draft so a double click cannot import the file twice.
    locked = await db.execute(
        select(Import)
        .where(Import.id == import_id, Import.owner_id == user.id)
        .with_for_update()
    )
    if locked.scalars().first() is None:
        raise HTTPException(status_code=404, detail="Import session not found")
    draft = await get_draft(db, user.id, import_id)

    plan = await _build_plan(db, user, draft, decisions)
    txns, problems = _transactions(plan)
    fresh, duplicates = await _split_duplicates(db, user.id, txns)
    selected = fresh if decisions.skip_duplicates else txns

    try:
        ids = await _create_entities(db, user, plan, draft.id)
        deltas = Deltas()
        expenses: list[dict[str, Any]] = []
        incomes: list[dict[str, Any]] = []
        transfers: list[dict[str, Any]] = []
        unmatched = 0

        for txn in selected:
            row = txn.row
            amount = float(row.amount)
            account_id = _resolve(txn.account, ids)
            if row.kind == "transfer":
                to_id = _resolve(txn.to_account, ids)
                transfers.append(
                    {
                        "amount": amount,
                        "date": row.date,
                        "description": row.full_description,
                        "from_acc": account_id,
                        "to_acc": to_id,
                        "owner_id": user.id,
                        "import_id": draft.id,
                    }
                )
                deltas.account[account_id]["tout"] += amount
                deltas.account[to_id]["tin"] += amount
                continue

            category_id = _resolve(txn.category, ids)
            subcategory_id = _resolve(txn.subcategory, ids)
            place_id = _resolve(txn.place, ids)
            unmatched += _uncategorized(txn)
            common = {
                "amount": amount,
                "date": row.date,
                "description": row.full_description,
                "owner_id": user.id,
                "account_id": account_id,
                "subcategory_id": subcategory_id,
                "place_id": place_id,
                "import_id": draft.id,
                "made_from": MADE_FROM,
            }
            if row.kind == "expense":
                expenses.append({**common, "category_id": category_id})
                deltas.expenses += amount
                if account_id is not None:
                    deltas.account[account_id]["exp"] += amount
                if category_id is not None:
                    deltas.category[category_id] += amount
            else:
                incomes.append(common)
                deltas.incomes += amount
                if account_id is not None:
                    deltas.account[account_id]["inc"] += amount
                if category_id is not None:
                    deltas.category[category_id] += amount
            if subcategory_id is not None:
                deltas.subcategory[subcategory_id] += amount

        if expenses:
            await db.execute(insert(Expense), expenses)
        if incomes:
            await db.execute(insert(Income), incomes)
        if transfers:
            await db.execute(insert(Transfer), transfers)
        await apply_deltas(db, user.id, deltas)

        if decisions.save_profile:
            await _save_profile(db, user, draft, plan, ids, decisions.profile_name)

        rows_failed = len(plan.parsed.errors) + len(problems)
        total = len(expenses) + len(incomes) + len(transfers)
        draft.status = ImportStatus.completed
        draft.staged_payload = None
        draft.expires_at = None
        draft.ended_at = _now()
        draft.mapping = {
            **(draft.mapping or {}),
            "decisions": decisions.model_dump(mode="json"),
        }
        draft.total_rows_processed = plan.parsed.total_rows
        draft.total_transactions_imported = total
        draft.expenses_imported = len(expenses)
        draft.incomes_imported = len(incomes)
        draft.transfers_imported = len(transfers)
        draft.accounts_created = len(plan.new_accounts)
        draft.sites_created = len(plan.new_places)
        draft.categories_created = len(plan.new_categories)
        draft.subcategories_created = len(plan.new_subcategories)
        draft.duplicates_skipped = len(duplicates) if decisions.skip_duplicates else 0
        draft.rows_failed = rows_failed
        draft.unmatched_categories = unmatched
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return schemas.CommitResult(
        import_id=draft.id,
        message="Import completed",
        total_rows_processed=draft.total_rows_processed,
        total_transactions_imported=draft.total_transactions_imported,
        expenses_imported=draft.expenses_imported,
        incomes_imported=draft.incomes_imported,
        transfers_imported=draft.transfers_imported,
        accounts_created=draft.accounts_created,
        sites_created=draft.sites_created,
        categories_created=draft.categories_created,
        subcategories_created=draft.subcategories_created,
        duplicates_skipped=draft.duplicates_skipped,
        rows_failed=draft.rows_failed,
        unmatched_categories=draft.unmatched_categories,
    )


async def _save_profile(
    db: AsyncSession,
    user: User,
    draft: Import,
    plan: Plan,
    ids: dict[str, int],
    name: str | None,
) -> None:
    grid = decode_payload(draft.staged_payload)["grid"]
    headers = grid[0]
    stored = draft.mapping or {}
    profile = await _get_profile(db, user.id, draft.header_fingerprint)
    profile_name = clean_cell(name or "")[:120] or draft.filename or "Import"
    if profile is None:
        profile = ImportProfile(
            owner_id=user.id,
            header_fingerprint=draft.header_fingerprint,
            name=profile_name,
            headers=headers,
            column_mapping=stored["columns"],
            options=stored["options"],
            value_mappings=_value_mappings(plan, ids, {}),
            last_used_at=_now(),
        )
        db.add(profile)
    else:
        profile.name = profile_name if name else profile.name
        profile.headers = headers
        profile.column_mapping = stored["columns"]
        profile.options = stored["options"]
        profile.value_mappings = _value_mappings(
            plan, ids, profile.value_mappings or {}
        )
        profile.last_used_at = _now()


# ---------------------------------------------------------------------------
# 6. History / undo
# ---------------------------------------------------------------------------
async def list_imports(db: AsyncSession, owner_id: int) -> list[Import]:
    result = await db.execute(
        select(Import)
        .where(Import.owner_id == owner_id, Import.status != ImportStatus.draft)
        .where(
            or_(
                Import.status != ImportStatus.failed,
                Import.total_transactions_imported.isnot(None),
            )
        )
        .order_by(Import.created_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())


async def get_import(db: AsyncSession, owner_id: int, import_id: int) -> Import:
    result = await db.execute(
        select(Import).where(Import.id == import_id, Import.owner_id == owner_id)
    )
    found = result.scalars().first()
    if found is None or found.status == ImportStatus.draft:
        raise HTTPException(status_code=404, detail="Import not found")
    return found


async def _import_deltas(db: AsyncSession, owner_id: int, import_id: int) -> Deltas:
    deltas = Deltas()
    exp_filter = and_(Expense.import_id == import_id, Expense.owner_id == owner_id)
    inc_filter = and_(Income.import_id == import_id, Income.owner_id == owner_id)
    tr_filter = and_(Transfer.import_id == import_id, Transfer.owner_id == owner_id)

    for account_id, total in await db.execute(
        select(Expense.account_id, func.sum(Expense.amount))
        .where(exp_filter, Expense.account_id.isnot(None))
        .group_by(Expense.account_id)
    ):
        deltas.account[account_id]["exp"] += float(total)
    for category_id, total in await db.execute(
        select(Expense.category_id, func.sum(Expense.amount))
        .where(exp_filter, Expense.category_id.isnot(None))
        .group_by(Expense.category_id)
    ):
        deltas.category[category_id] += float(total)
    for subcategory_id, total in await db.execute(
        select(Expense.subcategory_id, func.sum(Expense.amount))
        .where(exp_filter, Expense.subcategory_id.isnot(None))
        .group_by(Expense.subcategory_id)
    ):
        deltas.subcategory[subcategory_id] += float(total)
    deltas.expenses = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(Expense.amount), 0)).where(exp_filter)
            )
        ).scalar()
    )

    for account_id, total in await db.execute(
        select(Income.account_id, func.sum(Income.amount))
        .where(inc_filter, Income.account_id.isnot(None))
        .group_by(Income.account_id)
    ):
        deltas.account[account_id]["inc"] += float(total)
    for subcategory_id, category_id, total in await db.execute(
        select(Income.subcategory_id, Subcategory.category_id, func.sum(Income.amount))
        .join(Subcategory, Subcategory.id == Income.subcategory_id)
        .where(inc_filter)
        .group_by(Income.subcategory_id, Subcategory.category_id)
    ):
        deltas.subcategory[subcategory_id] += float(total)
        if category_id is not None:
            deltas.category[category_id] += float(total)
    deltas.incomes = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(Income.amount), 0)).where(inc_filter)
            )
        ).scalar()
    )

    for account_id, total in await db.execute(
        select(Transfer.from_acc, func.sum(Transfer.amount))
        .where(tr_filter, Transfer.from_acc.isnot(None))
        .group_by(Transfer.from_acc)
    ):
        deltas.account[account_id]["tout"] += float(total)
    for account_id, total in await db.execute(
        select(Transfer.to_acc, func.sum(Transfer.amount))
        .where(tr_filter, Transfer.to_acc.isnot(None))
        .group_by(Transfer.to_acc)
    ):
        deltas.account[account_id]["tin"] += float(total)
    return deltas


async def undo_import(db: AsyncSession, user: User, import_id: int) -> Import:
    locked = await db.execute(
        select(Import)
        .where(Import.id == import_id, Import.owner_id == user.id)
        .with_for_update()
    )
    record = locked.scalars().first()
    if record is None or record.status == ImportStatus.draft:
        raise HTTPException(status_code=404, detail="Import not found")
    if record.status != ImportStatus.completed:
        raise HTTPException(status_code=409, detail="This import can't be undone")

    owner_id = user.id
    try:
        deltas = await _import_deltas(db, owner_id, import_id)
        await apply_deltas(db, owner_id, deltas, sign=-1)

        for model in (Expense, Income, Transfer):
            await db.execute(
                delete(model)
                .where(model.import_id == import_id, model.owner_id == owner_id)
                .execution_options(synchronize_session=False)
            )

        await _delete_unused_created(db, owner_id, import_id)

        record.status = ImportStatus.reverted
        record.reverted_at = _now()
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return record


async def _delete_unused_created(
    db: AsyncSession, owner_id: int, import_id: int
) -> None:
    """Remove accounts/places/categories this import created, unless used elsewhere."""
    await db.execute(
        delete(Subcategory)
        .where(
            Subcategory.import_id == import_id,
            Subcategory.owner_id == owner_id,
            ~exists().where(Expense.subcategory_id == Subcategory.id),
            ~exists().where(Income.subcategory_id == Subcategory.id),
        )
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(Category)
        .where(
            Category.import_id == import_id,
            Category.owner_id == owner_id,
            ~exists().where(Expense.category_id == Category.id),
            ~exists().where(Subcategory.category_id == Category.id),
        )
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(Place)
        .where(
            Place.import_id == import_id,
            Place.owner_id == owner_id,
            ~exists().where(Expense.place_id == Place.id),
            ~exists().where(Income.place_id == Place.id),
        )
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(Account)
        .where(
            Account.import_id == import_id,
            Account.owner_id == owner_id,
            ~exists().where(Expense.account_id == Account.id),
            ~exists().where(Income.account_id == Account.id),
            ~exists().where(
                or_(Transfer.from_acc == Account.id, Transfer.to_acc == Account.id)
            ),
            ~exists().where(BalanceAdjustment.account_id == Account.id),
            ~exists().where(Holding.account_id == Account.id),
            ~exists().where(InvestmentTransaction.account_id == Account.id),
            ~exists().where(User.default_account_id == Account.id),
        )
        .execution_options(synchronize_session=False)
    )


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
async def list_profiles(db: AsyncSession, owner_id: int) -> list[ImportProfile]:
    result = await db.execute(
        select(ImportProfile)
        .where(ImportProfile.owner_id == owner_id)
        .order_by(ImportProfile.last_used_at.desc().nullslast())
    )
    return list(result.scalars().all())


async def delete_profile(db: AsyncSession, owner_id: int, profile_id: int) -> None:
    result = await db.execute(
        delete(ImportProfile).where(
            ImportProfile.id == profile_id, ImportProfile.owner_id == owner_id
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Profile not found")
    await db.commit()
