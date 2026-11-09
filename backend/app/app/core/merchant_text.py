"""Merchant text: reduce bank descriptions to the words naming the counterparty.

Detection compares these normalized strings to group a user's transactions and
to resolve their merchant. Nothing here knows about series or scoring.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol

# Payment noise removed from descriptions before comparing them (es + en).
NOISE_WORDS = frozenset(
    {
        "pago",
        "pagos",
        "payment",
        "payments",
        "pmt",
        "compra",
        "compras",
        "purchase",
        "pos",
        "card",
        "tarjeta",
        "tdc",
        "tdd",
        "debit",
        "debito",
        "credit",
        "credito",
        "cargo",
        "cargos",
        "charge",
        "transferencia",
        "transfer",
        "spei",
        "recibo",
        "ref",
        "referencia",
        "reference",
        "visa",
        "mastercard",
        "mc",
        "amex",
        "online",
        "en",
        "linea",
        "www",
        "http",
        "https",
        "com",
        "net",
        "org",
        "mx",
        "es",
        "io",
    }
)

_URL_SCHEME = re.compile(r"\b(?:https?://|www\.)")
_DOMAIN = re.compile(
    r"\b([a-z0-9-]+)\.(?:com|net|org|io|co|mx|es|tv|app)(?:\.[a-z]{2})?\b"
)
_DATE = re.compile(r"\b\d{1,4}[/.-]\d{1,2}(?:[/.-]\d{1,4})?\b")
_CARD = re.compile(r"(?:\*+|x{2,})\d{2,}")
_DIGITS = re.compile(r"\b\d{3,}\b")
_PUNCT = re.compile(r"[^a-z0-9]+")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _is_reference(token: str) -> bool:
    """Mixed letters and digits, 6+ characters: an order or reference id."""
    return (
        len(token) >= 6
        and any(ch.isdigit() for ch in token)
        and any(ch.isalpha() for ch in token)
    )


def normalize_description(text: str | None) -> str:
    """Reduce a bank description to the words that identify the counterparty."""
    if not text:
        return ""
    value = _strip_accents(text.lower())
    value = _URL_SCHEME.sub(" ", value)
    value = _DOMAIN.sub(r"\1", value)
    value = _CARD.sub(" ", value)
    value = _DATE.sub(" ", value)
    value = _DIGITS.sub(" ", value)
    value = _PUNCT.sub(" ", value)
    tokens = [
        token
        for token in value.split()
        if token not in NOISE_WORDS and not _is_reference(token) and not token.isdigit()
    ]
    return " ".join(tokens)


def slugify(text: str) -> str:
    value = _PUNCT.sub("_", _strip_accents(text.lower())).strip("_")
    return value[:64]


class _Described(Protocol):
    description: str | None
    place_name: str | None


def text_key(point: _Described) -> str:
    """Normalized text used to resolve the merchant; empty if there is none."""
    return normalize_description(point.description) or normalize_description(
        point.place_name
    )
