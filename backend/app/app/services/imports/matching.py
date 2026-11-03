"""Suggest how each distinct value in the file maps to the user's data."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from fuzzywuzzy import fuzz

from app.services.imports.parsing import ParsedRow, normalize_text
from app.synonyms import get_synonyms

MATCH_THRESHOLD = 85

_SYNONYMS = {normalize_text(k): normalize_text(v) for k, v in get_synonyms().items()}


@dataclass
class NamedEntity:
    id: int
    name: str


@dataclass
class SubcategoryEntity:
    id: int
    name: str
    category_id: int


@dataclass
class CategoryEntity:
    id: int
    name: str
    is_income: bool
    subcategories: list[SubcategoryEntity] = field(default_factory=list)


def value_key(value: str) -> str:
    return normalize_text(value)


def category_key(kind: str, category: str, subcategory: str) -> str:
    return f"{kind}|{normalize_text(category)}|{normalize_text(subcategory)}"


def _score(a: str, b: str) -> int:
    a_norm, b_norm = normalize_text(a), normalize_text(b)
    if not a_norm or not b_norm:
        return 0
    if a_norm == b_norm:
        return 100
    candidates = {a_norm, _SYNONYMS.get(a_norm, a_norm)}
    best = 0
    for candidate in candidates:
        if candidate == b_norm:
            return 99
        best = max(
            best,
            fuzz.ratio(candidate, b_norm),
            fuzz.token_sort_ratio(candidate, b_norm),
        )
    return best


def _best(value: str, entities: list[Any]) -> tuple[Any | None, int]:
    best, best_score = None, 0
    for entity in entities:
        score = _score(value, entity.name)
        if score > best_score:
            best, best_score = entity, score
    if best_score < MATCH_THRESHOLD:
        return None, best_score
    return best, best_score


# ---------------------------------------------------------------------------
# Accounts / places
# ---------------------------------------------------------------------------
def suggest_named(
    value: str, entities: list[NamedEntity], saved: dict[str, Any] | None
) -> dict[str, Any]:
    saved_decision = (saved or {}).get(value_key(value))
    if saved_decision and saved_decision.get("action") == "match":
        if any(e.id == saved_decision.get("id") for e in entities):
            return {**saved_decision, "score": 100, "source": "profile"}
    match, score = _best(value, entities)
    if match:
        return {"action": "match", "id": match.id, "score": score, "source": "auto"}
    return {"action": "create", "name": value[:100], "score": 0, "source": "auto"}


def collect_accounts(rows: list[ParsedRow]) -> Counter:
    counter: Counter = Counter()
    for row in rows:
        if row.account:
            counter[row.account] += 1
        if row.kind == "transfer" and row.to_account:
            counter[row.to_account] += 1
    return counter


def collect_places(rows: list[ParsedRow]) -> Counter:
    return Counter(row.place for row in rows if row.place and row.kind != "transfer")


def group_values(counter: Counter) -> list[dict[str, Any]]:
    """Merge values that only differ in case/accents into one entry."""
    grouped: dict[str, dict[str, Any]] = {}
    for value, count in counter.most_common():
        key = value_key(value)
        if key in grouped:
            grouped[key]["rows"] += count
        else:
            grouped[key] = {"key": key, "value": value, "rows": count}
    return list(grouped.values())


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
def collect_categories(rows: list[ParsedRow]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.kind == "transfer" or not (row.category or row.subcategory):
            continue
        key = category_key(row.kind, row.category, row.subcategory)
        if key in grouped:
            grouped[key]["rows"] += 1
        else:
            grouped[key] = {
                "key": key,
                "kind": row.kind,
                "category": row.category,
                "subcategory": row.subcategory,
                "rows": 1,
            }
    return sorted(grouped.values(), key=lambda item: -item["rows"])


def _valid_saved_category(
    decision: dict[str, Any], kind: str, categories: list[CategoryEntity]
) -> bool:
    if decision.get("action") != "match":
        return False
    category = next(
        (c for c in categories if c.id == decision.get("category_id")), None
    )
    if category is None or category.is_income != (kind == "income"):
        return False
    sub_id = decision.get("subcategory_id")
    if sub_id is None:
        return kind == "expense"
    return any(s.id == sub_id for s in category.subcategories)


def suggest_category(
    kind: str,
    category: str,
    subcategory: str,
    categories: list[CategoryEntity],
    saved: dict[str, Any] | None,
) -> dict[str, Any]:
    key = category_key(kind, category, subcategory)
    saved_decision = (saved or {}).get(key)
    if saved_decision and _valid_saved_category(saved_decision, kind, categories):
        return {**saved_decision, "score": 100, "source": "profile"}

    pool = [c for c in categories if c.is_income == (kind == "income")]
    leaf = subcategory or category

    parent, parent_score = _best(category, pool) if category else (None, 0)

    # 1. Parent matched: look for the child inside it.
    if parent is not None:
        if subcategory:
            child, child_score = _best(subcategory, parent.subcategories)
            if child is not None:
                return _match(parent.id, child.id, min(parent_score, child_score))
            return {
                "action": "create_subcategory",
                "category_id": parent.id,
                "name": subcategory[:100],
                "score": parent_score,
                "source": "auto",
            }
        if kind == "expense":
            return _match(parent.id, None, parent_score)
        # Incomes are stored against a subcategory.
        child, child_score = _best(category, parent.subcategories)
        if child is not None:
            return _match(parent.id, child.id, child_score)
        if parent.subcategories:
            return _match(parent.id, parent.subcategories[0].id, parent_score - 10)

    # 2. Match the most specific value against every subcategory.
    all_subs = [(c, s) for c in pool for s in c.subcategories]
    best_pair, best_score = None, 0
    for owner, sub in all_subs:
        score = _score(leaf, sub.name)
        if score > best_score:
            best_pair, best_score = (owner, sub), score
    if best_pair is not None and best_score >= MATCH_THRESHOLD:
        return _match(best_pair[0].id, best_pair[1].id, best_score)

    # 3. Nothing good: propose creating it.
    if parent is not None:
        return {
            "action": "create_subcategory",
            "category_id": parent.id,
            "name": leaf[:100],
            "score": parent_score,
            "source": "auto",
        }
    return {
        "action": "create_category",
        "name": (category or leaf)[:100],
        "subcategory_name": (subcategory or (leaf if kind == "income" else ""))[:100]
        or None,
        "score": 0,
        "source": "auto",
    }


def _match(category_id: int, subcategory_id: int | None, score: int) -> dict[str, Any]:
    return {
        "action": "match",
        "category_id": category_id,
        "subcategory_id": subcategory_id,
        "score": max(score, 0),
        "source": "auto",
    }
