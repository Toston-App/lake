"""AI steps of recurring detection: merchant resolution and classification.

Both are optional. Callers get None when the AI call fails (timeout, provider
or validation error) and detection carries on with the deterministic fallback,
as it does for any item the AI left unanswered.

Privacy: only normalized descriptions, place names, category names, the
transaction type and the series' amounts and schedule are sent; never account
names, ids or the user's email. Descriptions are free text the user wrote or
imported, so they may still contain names (e.g. "transfer to Ana"). Requests
are routed only to providers that don't store data (`data_collection: deny`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.core.config import settings
from app.utilities.observability import extract_cost_usd

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 8_192


@dataclass(frozen=True)
class MerchantQuery:
    """One normalized description to resolve to a merchant."""

    type: str
    text: str
    place: str | None


@dataclass(frozen=True)
class CandidateSummary:
    """What the classifier sees of one detected series."""

    type: str
    merchant_name: str
    category_name: str | None
    frequency: str
    interval: int
    occurrences: int
    amount_min: float
    amount_max: float


class _Indexed(BaseModel):
    i: int


T = TypeVar("T", bound=_Indexed)


class _Batch(BaseModel, Generic[T]):
    items: list[T]


class MerchantItem(_Indexed):
    merchant_key: str = Field(description='Lowercase ascii slug, e.g. "netflix"')
    merchant_name: str = Field(description='Display name, e.g. "Netflix"')


class MerchantBatch(_Batch[MerchantItem]):
    pass


class CandidateLabel(_Indexed):
    kind: Literal["subscription", "bill", "salary", "transfer", "habit", "other"]
    is_commitment: bool
    confidence: float = Field(ge=0, le=1)


class CandidateBatch(_Batch[CandidateLabel]):
    pass


MERCHANT_INSTRUCTIONS = """\
You group bank transaction descriptions by the real-world counterparty.
Each input item has an index `i`, the transaction `type`, a normalized
description `text` and an optional `place` name. Return one item per input
with a `merchant_key` and a `merchant_name`.

Rules:
- Texts that refer to the same real-world counterparty get the same
  merchant_key (e.g. "netflix" and "netflix subscription" -> "netflix").
- Different products from the same company stay separate only when they are
  clearly different services (e.g. "amazon" vs "amazon_prime").
- Generic texts get a generic key: "rent", "alquiler", "renta" -> "rent".
- merchant_key is a lowercase ascii slug using underscores.
- If you are unsure, return the input text as the slug. Never invent a
  merchant that is not in the text or place.
"""

CLASSIFY_INSTRUCTIONS = """\
You review recurring series found in a person's transactions and decide
whether each one is a commitment they would want to track as a recurring
transaction (a subscription, a bill, rent, a salary, a regular transfer), or a
spending habit (coffee shops, groceries, fuel, restaurants) that happens to be
regular.

Each input item has an index `i`, the transaction type, the merchant and
category names, the schedule and the amounts. Return one item per input:
- kind: subscription, bill, salary, transfer, habit or other
- is_commitment: true for subscriptions, bills, rent, salaries, loan or
  insurance payments and regular transfers; false for habits
- confidence: 0-1, how sure you are of is_commitment
"""

merchant_agent: Agent[None, MerchantBatch] = Agent(
    output_type=MerchantBatch,
    instructions=MERCHANT_INSTRUCTIONS,
    retries=1,
)
classify_agent: Agent[None, CandidateBatch] = Agent(
    output_type=CandidateBatch,
    instructions=CLASSIFY_INSTRUCTIONS,
    retries=1,
)


@dataclass
class AiStats:
    calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "errors": self.errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "model": self.model,
        }


def model_name() -> str:
    return settings.RECURRING_DETECTION_MODEL or settings.OPENROUTER_MODEL


def build_model() -> OpenRouterModel:
    return OpenRouterModel(
        model_name(),
        provider=OpenRouterProvider(
            api_key=settings.OPENROUTER_API_KEY,
            app_url=settings.OPENROUTER_SITE_URL,
            app_title=settings.OPENROUTER_APP_NAME,
        ),
        settings={
            "openrouter_usage": {"include": True},
            # Only providers that don't retain or train on the prompts.
            "openrouter_provider": {"data_collection": "deny"},
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
        },
    )


async def _run(agent: Agent, prompt: str, stats: AiStats) -> Any | None:
    stats.calls += 1
    stats.model = model_name()
    try:
        result = await asyncio.wait_for(
            agent.run(prompt, model=build_model()),
            timeout=settings.RECURRING_DETECTION_AI_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # timeouts, provider and validation errors
        stats.errors += 1
        logger.warning("recurring detection AI call failed: %s", exc)
        return None
    usage = result.usage
    stats.input_tokens += usage.input_tokens or 0
    stats.output_tokens += usage.output_tokens or 0
    for message in result.all_messages():
        if isinstance(message, ModelResponse):
            stats.cost_usd += extract_cost_usd(message) or 0.0
    return result.output


async def _run_indexed(
    agent: Agent[None, _Batch[T]], items: Sequence[Any], stats: AiStats
) -> dict[int, T] | None:
    """Send `items` by position; map index -> answer, or None if the call failed.

    Each item goes out as `{"i": index, **fields}`. Missing answers and
    answers for indexes that weren't sent are left out. No items, no call.
    """
    if not items:
        return {}
    payload = [{"i": i, **asdict(item)} for i, item in enumerate(items)]
    output = await _run(agent, json.dumps(payload, ensure_ascii=False), stats)
    if output is None:
        return None
    return {item.i: item for item in output.items if 0 <= item.i < len(items)}


async def resolve_merchants(
    items: Sequence[MerchantQuery], stats: AiStats
) -> dict[int, MerchantItem] | None:
    """Map item index -> merchant; None if the call failed."""
    return await _run_indexed(merchant_agent, items, stats)


# The classifier may call a series a habit; suggestions only tell commitments
# apart, so a habit is reported as "other".
_REPORTED_KIND = {"habit": "other"}


async def classify_candidates(
    items: Sequence[CandidateSummary], stats: AiStats
) -> dict[int, CandidateLabel] | None:
    """Map item index -> label with the kind as reported; None if the call
    failed."""
    labels = await _run_indexed(classify_agent, items, stats)
    if labels is None:
        return None
    return {
        i: label.model_copy(update={"kind": _REPORTED_KIND.get(label.kind, label.kind)})
        for i, label in labels.items()
    }
