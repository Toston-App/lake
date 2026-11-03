"""Turn a grid of strings into normalized transactions.

Pure functions only (no DB, no I/O) so they are easy to test.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.schemas.import_mapping import (  # noqa: F401 (re-exported)
    FIELDS,
    ColumnMapping,
    DateFormat,
    ParseOptions,
)

Kind = Literal["expense", "income", "transfer"]

MAX_AMOUNT = Decimal("1000000000")


DATE_FORMATS: dict[str, list[str]] = {
    "YYYY-MM-DD": ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"],
    "DD/MM/YYYY": ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"],
    "MM/DD/YYYY": ["%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y", "%m-%d-%y"],
    "DD MMM YYYY": ["%d %b %Y", "%d-%b-%Y", "%d/%b/%Y", "%d %B %Y", "%d %b %y"],
    "MMM DD, YYYY": ["%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"],
}
DEFAULT_TYPE_WORDS: dict[str, list[str]] = {
    "expense": [
        "expense",
        "expenses",
        "gasto",
        "gastos",
        "egreso",
        "egresos",
        "cargo",
        "debit",
        "debito",
        "withdrawal",
        "retiro",
        "salida",
        "out",
        "payment",
        "pago",
        "compra",
        "purchase",
    ],
    "income": [
        "income",
        "incomes",
        "ingreso",
        "ingresos",
        "abono",
        "credit",
        "credito",
        "deposit",
        "deposito",
        "entrada",
        "in",
        "refund",
        "reembolso",
    ],
    "transfer": [
        "transfer",
        "transfers",
        "transferencia",
        "transferencias",
        "traspaso",
        "traspasos",
        "(transfer)",
    ],
}


@dataclass
class ParsedRow:
    rows: list[int]  # 1-based line numbers in the file (header is line 1)
    kind: Kind
    date: date
    amount: Decimal
    title: str = ""
    description: str = ""
    account: str = ""
    to_account: str = ""
    category: str = ""
    subcategory: str = ""
    place: str = ""

    @property
    def full_description(self) -> str:
        parts = [self.title, self.description]
        text = " - ".join(p for p in parts if p)
        return text[:1000]


@dataclass
class RowError:
    row: int
    message: str


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    errors: list[RowError]
    date_format: str
    total_rows: int
    ignored_rows: int = 0
    counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------
def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.lower().split())


def _normalize_header(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"^\(?\d+\)\s*", "", value)  # "(1)Type" -> "type"
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return " ".join(value.split())


_HEADER_ALIASES: dict[str, list[str]] = {
    "date": [
        "date",
        "fecha",
        "transaction date",
        "fecha de operacion",
        "fecha operacion",
        "fecha de movimiento",
        "fecha movimiento",
        "posted date",
        "posting date",
        "booking date",
        "fecha valor",
        "dia",
        "day",
        "created",
        "fecha de transaccion",
    ],
    "amount": [
        "amount",
        "monto",
        "importe",
        "cantidad",
        "valor",
        "value",
        "total",
        "sum",
        "transaction amount",
        "monto total",
    ],
    "debit": [
        "debit",
        "debits",
        "debito",
        "cargo",
        "cargos",
        "retiro",
        "retiros",
        "withdrawal",
        "withdrawals",
        "money out",
        "paid out",
        "salida",
        "salidas",
        "outflow",
        "egreso",
        "egresos",
    ],
    "credit": [
        "credit",
        "credits",
        "credito",
        "abono",
        "abonos",
        "deposito",
        "depositos",
        "deposit",
        "deposits",
        "money in",
        "paid in",
        "entrada",
        "entradas",
        "inflow",
    ],
    "type": [
        "type",
        "tipo",
        "transaction type",
        "tipo de movimiento",
        "tipo movimiento",
        "tipo de transaccion",
        "kind",
    ],
    "title": [
        "title",
        "titulo",
        "payee",
        "item or payee",
        "concepto",
        "merchant",
        "comercio",
        "beneficiario",
        "name",
        "nombre",
        "counterparty",
    ],
    "description": [
        "description",
        "descripcion",
        "notes",
        "notas",
        "note",
        "nota",
        "memo",
        "details",
        "detalle",
        "detalles",
        "comments",
        "comentarios",
        "referencia",
        "reference",
    ],
    "account": [
        "account",
        "cuenta",
        "from account",
        "cuenta origen",
        "source account",
        "wallet",
        "billetera",
        "account name",
        "nombre de cuenta",
    ],
    "to_account": [
        "to account",
        "cuenta destino",
        "destination account",
        "target account",
        "to",
        "hacia",
        "destino",
    ],
    "category": [
        "category",
        "categoria",
        "parent category",
        "categoria padre",
        "categoria principal",
        "main category",
        "category group",
        "grupo",
    ],
    "subcategory": [
        "subcategory",
        "subcategoria",
        "sub category",
        "sub categoria",
        "child category",
    ],
    "place": [
        "place",
        "lugar",
        "site",
        "sitio",
        "location",
        "ubicacion",
        "store",
        "tienda",
        "establecimiento",
    ],
}
_ALIAS_LOOKUP = {
    alias: field_name
    for field_name, aliases in _HEADER_ALIASES.items()
    for alias in aliases
}
_PARENT_CATEGORY_ALIASES = {
    "parent category",
    "categoria padre",
    "categoria principal",
    "main category",
    "category group",
    "grupo",
}


def detect_mapping(headers: list[str]) -> ColumnMapping:
    normalized = [_normalize_header(h) for h in headers]
    has_parent_category = any(h in _PARENT_CATEGORY_ALIASES for h in normalized)
    assigned: dict[str, int] = {}
    for index, header in enumerate(normalized):
        field_name = _ALIAS_LOOKUP.get(header)
        if field_name is None:
            continue
        # Bluecoins style: "Parent Category" + "Category" (the child).
        if (
            field_name == "category"
            and has_parent_category
            and header not in _PARENT_CATEGORY_ALIASES
        ):
            field_name = "subcategory"
        if field_name not in assigned:
            assigned[field_name] = index
    if "amount" in assigned and ("debit" in assigned or "credit" in assigned):
        assigned.pop("debit", None)
        assigned.pop("credit", None)
    return ColumnMapping(**assigned)


def header_fingerprint(headers: list[str]) -> str:
    import hashlib

    joined = "\x1f".join(_normalize_header(h) for h in headers)
    return hashlib.sha256(joined.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------
_SPANISH_MONTHS = {
    "ene": "jan",
    "feb": "feb",
    "mar": "mar",
    "abr": "apr",
    "may": "may",
    "jun": "jun",
    "jul": "jul",
    "ago": "aug",
    "sep": "sep",
    "sept": "sep",
    "set": "sep",
    "oct": "oct",
    "nov": "nov",
    "dic": "dec",
    "enero": "january",
    "febrero": "february",
    "marzo": "march",
    "abril": "april",
    "mayo": "may",
    "junio": "june",
    "julio": "july",
    "agosto": "august",
    "septiembre": "september",
    "setiembre": "september",
    "octubre": "october",
    "noviembre": "november",
    "diciembre": "december",
}
_TIME_SUFFIX = re.compile(
    r"[T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*(z|[ap]\.?m\.?|[+-]\d{2}:?\d{2})?$"
)


def _prepare_date(value: str) -> str:
    text = normalize_text(value).replace(" de ", " ")
    text = _TIME_SUFFIX.sub("", text).strip()
    words = re.split(r"([\s\-/.,]+)", text)
    return "".join(_SPANISH_MONTHS.get(word, word) for word in words)


def parse_date(value: str, fmt: str) -> date | None:
    text = _prepare_date(value)
    if not text:
        return None
    for pattern in DATE_FORMATS[fmt]:
        try:
            parsed = datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        if 1900 <= parsed.year <= 2100:
            return parsed
    return None


def detect_date_format(values: list[str]) -> str:
    sample = [v for v in values if v][:500]
    if not sample:
        return "YYYY-MM-DD"
    scores = {
        fmt: sum(1 for v in sample if parse_date(v, fmt) is not None)
        for fmt in DATE_FORMATS
    }
    # Ties go to the earlier (more common for our users) format: ISO, then
    # day-first, then month-first.
    return max(DATE_FORMATS, key=lambda fmt: scores[fmt])


_AMOUNT_JUNK = re.compile(r"[^\d,.\-+()]")
_CURRENCY_SYMBOLS = re.compile(r"[$€£¥₩₹\s'\u00a0]")
_LEADING_CODE = re.compile(r"^[A-Za-z]{1,4}\.?")
_TRAILING_CODE = re.compile(r"[A-Za-z]{1,4}\.?$")


def parse_amount(value: str, decimal_separator: str) -> Decimal | None:
    text = (value or "").strip().replace("\u2212", "-")
    if not text:
        return None
    negative = False
    upper = text.upper()
    if upper.endswith(("DR", "CR")):
        negative = upper.endswith("DR")
        text = text[:-2]
    # Remove currency symbols / codes around the number ("MXN", "USD", "$"),
    # but never letters inside it: "1e9999" must be rejected, not read as 19999.
    text = _CURRENCY_SYMBOLS.sub("", text)
    text = _TRAILING_CODE.sub("", _LEADING_CODE.sub("", text))
    if text.startswith(("-", "+")) and len(text) > 1:
        text = text[0] + _LEADING_CODE.sub("", text[1:])
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.endswith("-"):
        negative = True
        text = text[:-1]
    if text.startswith("-"):
        negative = not negative
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if decimal_separator == ",":
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    if not text or not re.fullmatch(r"\d+(\.\d+)?|\.\d+", text):
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if amount >= MAX_AMOUNT:
        return None
    return -amount if negative else amount


def detect_decimal_separator(values: list[str]) -> str:
    comma = dot = 0
    for value in values[:500]:
        text = _AMOUNT_JUNK.sub("", value or "")
        if re.search(r",\d{1,2}$", text) and not re.search(r"\.\d{1,2}$", text):
            comma += 1
        elif re.search(r"\.\d{1,2}$", text):
            dot += 1
        elif re.search(r"\d\.\d{3}(\.\d{3})*$", text) and "," not in text:
            # "1.234" with no decimals: thousands separator in comma locales.
            comma += 1
    return "," if comma > dot else "."


def detect_category_separator(values: list[str]) -> str | None:
    sample = [v for v in values if v][:500]
    if not sample:
        return None
    for separator in (">", ":", "|"):
        hits = sum(1 for v in sample if separator in v)
        if hits >= max(1, len(sample) // 2):
            return separator
    return None


def _type_lookup() -> dict[str, str]:
    return {
        normalize_text(word): kind
        for kind, words in DEFAULT_TYPE_WORDS.items()
        for word in words
    }


_TYPE_LOOKUP = _type_lookup()


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def validate_mapping(mapping: ColumnMapping, column_count: int) -> list[str]:
    problems: list[str] = []
    used = mapping.used_columns()
    for name, index in used.items():
        if index < 0 or index >= column_count:
            problems.append(f"Column for '{name}' does not exist in the file.")
    if mapping.date is None:
        problems.append("Select the column that contains the date.")
    if mapping.amount is None and mapping.debit is None and mapping.credit is None:
        problems.append("Select the amount column (or debit / credit columns).")
    return problems


@dataclass
class _Candidate:
    line: int
    kind: Kind | None
    date: date
    signed_amount: Decimal
    values: dict[str, str]


def parse_rows(
    grid: list[list[str]], mapping: ColumnMapping, options: ParseOptions
) -> ParseResult:
    """Parse every data row. The first row (after skip_rows) is the header."""
    body = grid[options.skip_rows + 1 :]
    first_line = options.skip_rows + 2
    date_format = options.date_format
    if date_format == "auto":
        date_format = detect_date_format([_cell(r, mapping.date) for r in body])

    errors: list[RowError] = []
    candidates: list[_Candidate] = []
    ignored = 0

    for offset, row in enumerate(body):
        line = first_line + offset
        raw_date = _cell(row, mapping.date)
        parsed_date = parse_date(raw_date, date_format)
        if parsed_date is None:
            if not raw_date and not any(
                _cell(row, i) for i in (mapping.amount, mapping.debit, mapping.credit)
            ):
                ignored += 1  # footer / blank-ish row
                continue
            errors.append(RowError(line, f"Invalid date '{raw_date[:40]}'."))
            continue

        signed = _signed_amount(row, mapping, options)
        if isinstance(signed, str):
            errors.append(RowError(line, signed))
            continue
        if signed == 0:
            errors.append(RowError(line, "Amount is zero."))
            continue

        raw_type = normalize_text(_cell(row, mapping.type))
        kind: Kind | None = _TYPE_LOOKUP.get(raw_type) if raw_type else None  # type: ignore[assignment]

        category = _cell(row, mapping.category)
        subcategory = _cell(row, mapping.subcategory)
        if options.category_separator and category and not subcategory:
            parent, sep, child = category.partition(options.category_separator)
            if sep:
                category, subcategory = parent.strip(), child.strip()
        if not category and subcategory:
            category, subcategory = subcategory, ""
        if category.strip().lower() in {"(transfer)", "transfer", "transferencia"}:
            category = ""
            kind = "transfer"

        candidates.append(
            _Candidate(
                line=line,
                kind=kind,
                date=parsed_date,
                signed_amount=signed,
                values={
                    "title": _cell(row, mapping.title),
                    "description": _cell(row, mapping.description),
                    "account": _cell(row, mapping.account),
                    "to_account": _cell(row, mapping.to_account),
                    "category": category[:200],
                    "subcategory": subcategory[:200],
                    "place": _cell(row, mapping.place)[:200],
                },
            )
        )

    rows: list[ParsedRow] = []
    transfer_candidates: list[_Candidate] = []
    for candidate in candidates:
        if candidate.kind == "transfer":
            transfer_candidates.append(candidate)
            continue
        kind: Kind = candidate.kind or (
            "expense" if candidate.signed_amount < 0 else "income"
        )
        rows.append(_to_row(candidate, kind))

    transfers, transfer_errors = _pair_transfers(transfer_candidates)
    rows.extend(transfers)
    errors.extend(transfer_errors)
    rows.sort(key=lambda r: r.rows[0])
    errors.sort(key=lambda e: e.row)

    counts = defaultdict(int)
    for row in rows:
        counts[row.kind] += 1
    return ParseResult(
        rows=rows,
        errors=errors,
        date_format=date_format,
        total_rows=len(body) - ignored,
        ignored_rows=ignored,
        counts=dict(counts),
    )


def _signed_amount(
    row: list[str], mapping: ColumnMapping, options: ParseOptions
) -> Decimal | str:
    sep = options.decimal_separator
    if mapping.amount is not None:
        raw = _cell(row, mapping.amount)
        amount = parse_amount(raw, sep)
        if amount is None:
            return f"Invalid amount '{raw[:40]}'." if raw else "Amount is empty."
    else:
        raw_debit = _cell(row, mapping.debit)
        raw_credit = _cell(row, mapping.credit)
        debit = parse_amount(raw_debit, sep) if raw_debit else None
        credit = parse_amount(raw_credit, sep) if raw_credit else None
        if raw_debit and debit is None:
            return f"Invalid debit amount '{raw_debit[:40]}'."
        if raw_credit and credit is None:
            return f"Invalid credit amount '{raw_credit[:40]}'."
        if debit is None and credit is None:
            return "Amount is empty."
        amount = abs(credit or Decimal(0)) - abs(debit or Decimal(0))
    if options.invert_sign:
        amount = -amount
    return amount.quantize(Decimal("0.01"))


def _to_row(candidate: _Candidate, kind: Kind) -> ParsedRow:
    values = candidate.values
    return ParsedRow(
        rows=[candidate.line],
        kind=kind,
        date=candidate.date,
        amount=abs(candidate.signed_amount),
        title=values["title"],
        description=values["description"],
        account=values["account"],
        to_account=values["to_account"],
        category=values["category"] if kind != "transfer" else "",
        subcategory=values["subcategory"] if kind != "transfer" else "",
        place=values["place"] if kind != "transfer" else "",
    )


def _pair_transfers(
    candidates: list[_Candidate],
) -> tuple[list[ParsedRow], list[RowError]]:
    """Build transfers from rows marked as transfers.

    A row with a destination account is a full transfer by itself. Otherwise
    the file has one row per side (e.g. Bluecoins: -100 in A, +100 in B on the
    same date) and the two sides are paired.
    """
    rows: list[ParsedRow] = []
    errors: list[RowError] = []
    outgoing: dict[tuple[date, Decimal], list[_Candidate]] = defaultdict(list)
    incoming: dict[tuple[date, Decimal], list[_Candidate]] = defaultdict(list)

    for candidate in candidates:
        values = candidate.values
        if values["to_account"]:
            rows.append(_to_row(candidate, "transfer"))
            continue
        key = (candidate.date, abs(candidate.signed_amount))
        if candidate.signed_amount < 0:
            outgoing[key].append(candidate)
        else:
            incoming[key].append(candidate)

    for key, outs in outgoing.items():
        ins = incoming.get(key, [])
        for out in outs:
            match = next(
                (c for c in ins if c.values["account"] != out.values["account"]),
                None,
            )
            if match is None:
                errors.append(
                    RowError(
                        out.line,
                        "Transfer without its matching incoming row (same date and amount).",
                    )
                )
                continue
            ins.remove(match)
            row = _to_row(out, "transfer")
            row.rows = sorted([out.line, match.line])
            row.to_account = match.values["account"]
            if not row.title:
                row.title = match.values["title"]
            if not row.description:
                row.description = match.values["description"]
            rows.append(row)
    for ins in incoming.values():
        for leftover in ins:
            errors.append(
                RowError(
                    leftover.line,
                    "Transfer without its matching outgoing row (same date and amount).",
                )
            )
    return rows, errors
