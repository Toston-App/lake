"""AI steps of recurring detection: merchant resolution and classification.

Both are optional. Callers get an empty answer when the AI is disabled, slow or
failing, and detection carries on with the deterministic fallback.

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
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.core.config import settings
from app.utilities.observability import extract_cost_usd

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 8_192


class MerchantItem(BaseModel):
    i: int
    merchant_key: str = Field(description='Lowercase ascii slug, e.g. "netflix"')
    merchant_name: str = Field(description='Display name, e.g. "Netflix"')


class MerchantBatch(BaseModel):
    items: list[MerchantItem]


class CandidateLabel(BaseModel):
    i: int
    kind: Literal["subscription", "bill", "salary", "transfer", "habit", "other"]
    is_commitment: bool
    confidence: float = Field(ge=0, le=1)


class CandidateBatch(BaseModel):
    items: list[CandidateLabel]


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


async def _run(agent: Agent, payload: list[dict], stats: AiStats) -> Any | None:
    stats.calls += 1
    stats.model = model_name()
    try:
        result = await asyncio.wait_for(
            agent.run(json.dumps(payload, ensure_ascii=False), model=build_model()),
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


async def resolve_merchants(
    items: list[dict], stats: AiStats
) -> dict[int, MerchantItem]:
    """Map item index -> merchant; missing or unknown indexes are left out."""
    if not items:
        return {}
    output = await _run(merchant_agent, items, stats)
    if output is None:
        return {}
    known = {item["i"] for item in items}
    return {item.i: item for item in output.items if item.i in known}


async def classify_candidates(
    items: list[dict], stats: AiStats
) -> dict[int, CandidateLabel]:
    if not items:
        return {}
    output = await _run(classify_agent, items, stats)
    if output is None:
        return {}
    known = {item["i"] for item in items}
    return {item.i: item for item in output.items if item.i in known}
