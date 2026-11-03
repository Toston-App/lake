"""Column mapping / parse options for the file importer.

Kept in the schemas layer with no app imports: ``app.schemas`` must never
import ``app.services`` (services -> crud -> schemas would be circular).
"""

import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

FIELDS = (
    "date",
    "amount",
    "debit",
    "credit",
    "type",
    "title",
    "description",
    "account",
    "to_account",
    "category",
    "subcategory",
    "place",
)

DateFormat = Literal[
    "auto", "YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY", "DD MMM YYYY", "MMM DD, YYYY"
]


class ColumnMapping(BaseModel):
    """Index (0-based) of the file column used for each field, or None."""

    date: int | None = None
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    type: int | None = None
    title: int | None = None
    description: int | None = None
    account: int | None = None
    to_account: int | None = None
    category: int | None = None
    subcategory: int | None = None
    place: int | None = None

    def used_columns(self) -> dict[str, int]:
        return {
            name: index for name in FIELDS if (index := getattr(self, name)) is not None
        }


class ParseOptions(BaseModel):
    date_format: DateFormat = "auto"
    decimal_separator: Literal[".", ","] = "."
    # When true, positive amounts are expenses (e.g. credit card statements).
    invert_sign: bool = False
    # Used for rows whose account cell is empty (or when no account column).
    default_account_id: int | None = None
    # Split "Food > Groceries" style values into category / subcategory.
    category_separator: Literal[">", ":", "/", "|"] | None = None
    skip_rows: int = Field(default=0, ge=0, le=50)


_FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Co", "Cs"}


def clean_cell(value: object) -> str:
    """Normalize a cell to a bounded, printable string."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = unicodedata.normalize("NFKC", text)
    # Drop control / format / private-use characters (incl. bidi overrides),
    # keeping ordinary spaces. Tabs and newlines inside a cell become spaces.
    text = "".join(
        " " if ch in "\t\r\n" else ch
        for ch in text
        if ch in "\t\r\n" or unicodedata.category(ch) not in _FORBIDDEN_CATEGORIES
    )
    return " ".join(text.split())
