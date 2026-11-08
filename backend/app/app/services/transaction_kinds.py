"""What differs between expenses, incomes and transfers, in one table.

Recurring templates and detected series store every type in the same shape
(`account_id`, `to_account_id`, `category_id`, ...); each kind maps those
fields to its own columns (transfers use `from_acc` / `to_acc`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel

from app import crud, models, schemas
from app.core.recurrence_types import TxPoint
from app.core.transaction_type import TransactionType
from app.services import transactions as transaction_service

# `made_from` of the expenses and incomes a template generates.
MADE_FROM = "Recurring"


@dataclass(frozen=True)
class TransactionKind:
    type: TransactionType
    model: type
    crud: Any
    # Template field -> this kind's column, for the fields the kind has.
    columns: Mapping[str, str]
    # The template field (and its model) whose name labels a series that has
    # no description or place.
    name_source: tuple[str, type] | None
    # The create schema of a generated occurrence, from the copied fields.
    build_occurrence: Callable[[dict, date], BaseModel]
    # Template fields an occurrence can't be created without, and the error
    # that pauses the template when one of them is gone.
    required: tuple[str, ...] = ()
    missing_required: str = ""
    # References checked before generating, in this order: the expense and
    # income services silently drop a missing or foreign one, and a template
    # should pause instead (the transfer service rejects them itself).
    checked_references: tuple[str, ...] = ()

    def service(self, action: str) -> Callable[..., Awaitable[Any]]:
        """This kind's `create_*` / `delete_*` in the transactions service.

        Looked up on each call, so the service functions can be patched.
        """
        return getattr(transaction_service, f"{action}_{self.type.value}")

    def column(self, field: str):
        return getattr(self.model, self.columns[field])

    def template_fields(self, transaction: Any) -> dict:
        """The template fields a transaction of this kind carries."""
        return {f: getattr(transaction, c) for f, c in self.columns.items()}

    def occurrence_fields(self, template: Any) -> dict:
        """A template's fields under this kind's column names."""
        return {
            "amount": template.amount,
            "description": template.description,
            **{c: getattr(template, f) for f, c in self.columns.items()},
        }

    def to_point(self, transaction: Any, place_name: str | None = None) -> TxPoint:
        return TxPoint(
            id=transaction.id,
            type=self.type,
            date=transaction.date,
            amount=float(transaction.amount),
            description=transaction.description,
            place_name=place_name,
            **self.template_fields(transaction),
        )


KINDS: dict[TransactionType, TransactionKind] = {
    kind.type: kind
    for kind in (
        TransactionKind(
            type=TransactionType.expense,
            model=models.Expense,
            crud=crud.expense,
            columns={
                "account_id": "account_id",
                "category_id": "category_id",
                "subcategory_id": "subcategory_id",
                "place_id": "place_id",
            },
            name_source=("category_id", models.Category),
            build_occurrence=lambda fields, day: schemas.ExpenseCreate(
                **fields, date=day.isoformat(), made_from=MADE_FROM
            ),
            checked_references=(
                "account_id",
                "subcategory_id",
                "place_id",
                "category_id",
            ),
        ),
        TransactionKind(
            type=TransactionType.income,
            model=models.Income,
            crud=crud.income,
            # Incomes are categorised through their subcategory only.
            columns={
                "account_id": "account_id",
                "subcategory_id": "subcategory_id",
                "place_id": "place_id",
            },
            name_source=("subcategory_id", models.Subcategory),
            build_occurrence=lambda fields, day: schemas.IncomeCreate(
                **fields, date=day.isoformat(), made_from=MADE_FROM
            ),
            checked_references=("account_id", "subcategory_id", "place_id"),
        ),
        TransactionKind(
            type=TransactionType.transfer,
            model=models.Transfer,
            crud=crud.transfer,
            columns={"account_id": "from_acc", "to_account_id": "to_acc"},
            name_source=None,
            build_occurrence=lambda fields, day: schemas.TransferCreate(
                **fields, date=day
            ),
            required=("account_id", "to_account_id"),
            missing_required="One of the transfer accounts was deleted",
        ),
    )
}


def kind_of(type_: TransactionType | str) -> TransactionKind:
    return KINDS[TransactionType(type_)]
