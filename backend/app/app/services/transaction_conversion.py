"""Change a transaction's kind: expense, income or transfer.

Expenses, incomes and transfers are separate tables, so a conversion creates
the new record and then deletes the original, each through the transactions
service so account balances and category totals stay right. If deleting the
original fails, the new record is deleted again, so the user never ends up
with both.

What carries over: amount, date and description always. Account: an expense
or income keeps its account; becoming a transfer, it is the side the money
already moved on (an expense's account is the source, an income's the
destination) and the other side must be given. A transfer becoming an
expense uses its source account, and becoming an income its destination.
The place carries over between expenses and incomes. A category never
carries over, since expense and income categories differ; pass a new one.

A transaction that belongs to a recurring series is refused: the series
would lose track of it.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.schemas.transaction import TransactionType
from app.services.errors import DomainError
from app.services.ownership import require_owned
from app.services.transaction_kinds import KINDS

logger = logging.getLogger(__name__)

EXPENSE = TransactionType.expense
INCOME = TransactionType.income
TRANSFER = TransactionType.transfer


@dataclass(frozen=True)
class ConversionTargets:
    """Fields the caller may set on the new record."""

    account_id: int | None = None
    from_acc: int | None = None
    to_acc: int | None = None
    category_id: int | None = None
    subcategory_id: int | None = None
    place_id: int | None = None

    @classmethod
    def from_args(cls, args: Any) -> ConversionTargets:
        """From a tool call's arguments: a dict or an argument model."""
        get = args.get if isinstance(args, dict) else lambda k: getattr(args, k, None)
        return cls(**{f.name: get(f.name) for f in dataclasses.fields(cls)})


async def load_source(
    db: AsyncSession, user: models.User, from_type: TransactionType, transaction_id: int
) -> Any:
    """The user's transaction to convert; another user's is reported missing."""
    return await require_owned(
        db,
        user,
        KINDS[from_type].crud,
        transaction_id,
        "Transaction",
        hide_foreign=True,
        allow_superuser=False,
    )


def plan_conversion(
    source: Any,
    from_type: TransactionType,
    to_type: TransactionType,
    targets: ConversionTargets,
) -> dict:
    """The new record's fields, or DomainError when the conversion can't work."""
    if from_type == to_type:
        raise DomainError("The transaction already has that type")
    if source.recurring_id:
        raise DomainError(
            "This transaction belongs to a recurring series. Change or delete "
            "the series first."
        )
    fields: dict[str, Any] = {
        "amount": source.amount,
        "date": source.date.isoformat() if source.date else None,
        "description": source.description,
    }
    if to_type == TRANSFER:
        if from_type == EXPENSE:
            from_acc = targets.from_acc or source.account_id
            to_acc = targets.to_acc
        else:
            from_acc = targets.from_acc
            to_acc = targets.to_acc or source.account_id
        if not from_acc or not to_acc:
            missing = "to_acc" if from_type == EXPENSE else "from_acc"
            raise DomainError(f"A transfer needs both accounts: pass {missing}")
        if from_acc == to_acc:
            raise DomainError("A transfer needs two different accounts")
        return {**fields, "from_acc": from_acc, "to_acc": to_acc}

    if from_type == TRANSFER:
        account_id = targets.account_id or (
            source.from_acc if to_type == EXPENSE else source.to_acc
        )
        place_id = targets.place_id
    else:
        account_id = targets.account_id or source.account_id
        place_id = targets.place_id or source.place_id
    fields.update(
        account_id=account_id,
        subcategory_id=targets.subcategory_id,
        place_id=place_id,
    )
    if to_type == EXPENSE:
        fields["category_id"] = targets.category_id
    elif targets.category_id is not None:
        raise DomainError("Incomes have no category; pass a subcategory_id instead")
    return fields


def _create_schema(to_type: TransactionType, fields: dict, made_from: str):
    if to_type == EXPENSE:
        return schemas.ExpenseCreate(**fields, made_from=made_from)
    if to_type == INCOME:
        return schemas.IncomeCreate(**fields, made_from=made_from)
    return schemas.TransferCreate(**fields)


async def convert_transaction(
    db: AsyncSession,
    user: models.User,
    *,
    from_type: TransactionType,
    transaction_id: int,
    to_type: TransactionType,
    targets: ConversionTargets,
    made_from: str,
) -> Any:
    """Replace the transaction with one of ``to_type``; returns the new record."""
    source = await load_source(db, user, from_type, transaction_id)
    fields = plan_conversion(source, from_type, to_type, targets)
    created = await KINDS[to_type].service("create")(
        db, user, _create_schema(to_type, fields, made_from)
    )
    created_id = created.id
    try:
        await KINDS[from_type].service("delete")(db, user, transaction_id)
    except Exception:
        try:
            if not db.is_active:
                # A failed statement leaves the session unusable until rolled
                # back, and the rollback expires what the session had loaded.
                await db.rollback()
                await db.refresh(user)
            await KINDS[to_type].service("delete")(db, user, created_id)
        except Exception:
            logger.exception(
                "convert_transaction left both records: %s %s and %s %s",
                from_type.value,
                transaction_id,
                to_type.value,
                created_id,
            )
        raise
    return created
