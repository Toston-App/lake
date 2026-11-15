"""Pydantic AI finance agent with Vercel AI SDK streaming.

Replaces the hand-rolled OpenRouter tool-calling loop in ``chat_agent.py``.
The agent is driven by ``VercelAIAdapter`` (see ``api/api_v1/endpoints/chat.py``)
which speaks the Vercel AI UI Message Stream Protocol so the React frontend can
use ``@ai-sdk/react``'s ``useChat``.

Key mappings from the old harness:
- OpenRouter client            -> ``OpenRouterModel`` + ``OpenRouterProvider``
- ``execute_tool`` dispatch    -> typed tool functions on a ``FunctionToolset``
- read-only data tools         -> always return private model context and emit a
  ``DataChunk`` only for explicit ``user_visible`` presentation
- ``ask_user`` tool            -> defers immediately; the transport emits a
  ``data-pending-question`` from its validated input, and the frontend sends
  the answer as the deferred result on the next turn
- ``/confirm`` + Redis pending -> write/destructive tools do NOT execute during
  the agent run; they emit a ``data-pending-confirmation`` ``DataChunk``
  (built and stashed in Redis by ``confirmation.stage_action``). The frontend
  renders the editable approval card
  from that DataChunk and ``POST /conversations/{id}/confirm`` runs
  ``execute_tool`` with the (possibly edited) args. The v6 tool-approval
  protocol was rejected because it can't carry edited args back.
- per-round / per-tool telemetry -> ``Hooks`` capability
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic_ai import (
    Agent,
    CallDeferred,
    FunctionToolset,
    RunContext,
    ToolDefinition,
    ToolReturn,
)
from pydantic_ai.capabilities import Hooks
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings, merge_model_settings
from pydantic_ai.ui.vercel_ai.response_types import DataChunk, SourceUrlChunk
from pydantic_ai.usage import RunUsage, UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.ai.agent_tools import agent_tools
from app.ai.chat_prompt import build_system_prompt, load_bootstrap_context
from app.ai.confirmation import ToolExecutionError, stage_action
from app.ai.tools import (
    AskUserArgs,
    ReadPresentation,
    RenderDataArgs,
    SearchWebArgs,
    SubmitFeedbackArgs,
)
from app.ai.web_search import search_web as _search_web
from app.core.config import settings
from app.core.tiers import Tier, chat_model_for_tier
from app.services import feedback as feedback_service
from app.services.errors import ServiceError
from app.utilities.observability import (
    extract_cost_usd,
    record_llm_call,
    record_tool_call,
)

# Tool errors surfaced to the model as a normal (non-fatal) tool result rather
# than aborting the whole run (matches the old ``execute_tool`` try/except).
_TOOL_ERRORS = (ServiceError, ToolExecutionError, ValueError, KeyError)


@dataclass
class ChatDeps:
    """Per-request dependencies injected into every tool and instruction call."""

    db: AsyncSession
    user: models.User
    tier: Tier
    conversation_id: str
    local_date: date = field(default_factory=date.today)
    time_zone: str = "UTC"
    locale: str = "en"
    turn_id: int | None = None
    # Set to True by ``_defer_for_confirmation`` when a write/destructive tool
    # produced a confirmation draft this turn. Read by the endpoint's
    # ``on_complete`` to flag the observability turn as ``pending_confirmation``.
    had_pending_confirmation: bool = False
    # Populated by ``ask_user`` before it defers the call. The endpoint uses
    # this to persist the public question card alongside the private deferred
    # tool call.
    pending_question: dict[str, Any] | None = None
    pending_question_id: str | None = None


def build_model(model_name: str) -> OpenRouterModel:
    return OpenRouterModel(
        model_name,
        provider=OpenRouterProvider(
            api_key=settings.OPENROUTER_API_KEY,
            app_url=settings.OPENROUTER_SITE_URL,
            app_title=settings.OPENROUTER_APP_NAME,
        ),
        settings={
            # Without this, OpenRouter returns token counts but omits `usage.cost`.
            "openrouter_usage": {"include": True},
            "max_tokens": settings.CHAT_MAX_OUTPUT_TOKENS,
        },
    )


def build_usage_limits() -> UsageLimits:
    """Hard backstop: the configured tool rounds plus the final answer.

    There is deliberately no ``total_tokens_limit``. Pydantic AI checks it while
    a response streams, once the provider has already billed the call, so the
    user would get the whole answer and then an error. The token budget is
    enforced before each call instead (see ``turn_budget_spent``).
    """
    return UsageLimits(request_limit=settings.CHAT_MAX_TOOL_ROUNDS + 1)


def turn_budget_spent(usage: RunUsage) -> bool:
    """Whether the next model call must be the turn's final answer.

    True once the turn has used its tool rounds or its token budget. That call
    can't use tools, so a turn ends with a reply and goes over
    ``CHAT_MAX_TOKENS_PER_TURN`` by at most one call.
    """
    return (
        usage.requests >= settings.CHAT_MAX_TOOL_ROUNDS
        or usage.total_tokens >= settings.CHAT_MAX_TOKENS_PER_TURN
    )


_BUDGET_SPENT_INSTRUCTION = (
    "This turn has used its tool budget, so tools are unavailable. Answer now "
    "with the information you already have. If something is missing, say what "
    "and suggest a narrower follow-up question."
)


async def chat_instructions(ctx: RunContext[ChatDeps]) -> str:
    """Dynamic, server-owned instructions (never persisted to the client)."""
    bootstrap = await load_bootstrap_context(ctx.deps.db, ctx.deps.user)
    prompt = build_system_prompt(
        bootstrap,
        local_date=ctx.deps.local_date,
        time_zone=ctx.deps.time_zone,
    )
    if turn_budget_spent(ctx.usage):
        prompt = f"{prompt}\n\n{_BUDGET_SPENT_INSTRUCTION}"
    return prompt


agent = Agent(
    build_model(chat_model_for_tier(Tier.free)),
    deps_type=ChatDeps,
    output_type=str,
    instructions=chat_instructions,
    retries=1,
)


def get_chat_agent() -> Agent[ChatDeps, str]:
    return agent


# --------------------------------------------------------------------------- #
# Tool functions
# --------------------------------------------------------------------------- #
# Read-only data tools always return useful private model context. They attach a
# DataChunk matching a frontend component only when the model explicitly asks
# for user-visible presentation.
#
# Write / destructive tools return a plain string. Approval is gated dynamically
# by ``ApprovalRequiredToolset`` (see ``build_toolset``) which replicates the old
# ``requires_confirmation`` logic.


def _read_tool_return(
    return_value: Any,
    presentation: ReadPresentation,
    *ui_chunks: Any,
) -> ToolReturn:
    """Keep read data private unless the model deliberately requests UI."""
    return ToolReturn(
        return_value=return_value,
        metadata=list(ui_chunks) if presentation == "user_visible" else [],
    )


async def _defer_for_confirmation(
    ctx: RunContext[ChatDeps],
    tool_name: str,
    tool_args: dict[str, Any],
    response_language: str,
) -> ToolReturn:
    """Stash a pending action in Redis and emit a ``data-pending-confirmation``
    DataChunk so the frontend can render the editable approval card.

    The tool does NOT execute the mutation; that happens in
    ``POST /conversations/{id}/confirm`` via ``execute_tool`` once the user
    approves (possibly with edited args). Reuses ``build_pending_preview`` so the
    card gets the existing record + human-readable labels for update/delete.
    """
    staged = await stage_action(
        ctx.deps.db,
        ctx.deps.user,
        tool_name,
        tool_args,
        conversation_id=ctx.deps.conversation_id,
        response_language=response_language,
        turn_id=ctx.deps.turn_id,
    )
    ctx.deps.had_pending_confirmation = True
    return ToolReturn(
        return_value=staged.message,
        metadata=[
            DataChunk(
                type="data-pending-confirmation",
                data={
                    "action_id": staged.action_id,
                    "tool_name": tool_name,
                    "preview": staged.preview,
                    "message": staged.message,
                    "response_language": response_language,
                },
            )
        ],
    )


async def submit_feedback(
    ctx: RunContext[ChatDeps], payload: SubmitFeedbackArgs
) -> str:
    """Submit user feedback to the team."""
    obj = schemas.FeedbackCreate(message=payload.message, sentiment=payload.sentiment)
    await feedback_service.submit_feedback(ctx.deps.db, ctx.deps.user, obj)
    return "Feedback submitted. Thank you!"


async def search_web(
    ctx: RunContext[ChatDeps],  # noqa: ARG001
    payload: SearchWebArgs,
) -> ToolReturn:
    """Search the web for current finance information: market data, rates, news,
    regulations, definitions, and comparisons. Use for anything not in the user's
    personal data."""
    data = await _search_web(
        payload.query, topic=payload.topic, max_results=payload.max_results
    )
    results = data.get("results", []) if isinstance(data, dict) else []
    public_data = {
        "items": results,
        "answer": data.get("answer") if isinstance(data, dict) else None,
    }
    chunks: list[Any] = [
        DataChunk(
            type="data-web-sources",
            data=public_data,
        )
    ]
    for item in results:
        url = item.get("url") or ""
        if url:
            chunks.append(
                SourceUrlChunk(sourceId=url, url=url, title=item.get("title"))
            )
    return _read_tool_return(
        {**public_data, "total": len(results)},
        payload.presentation,
        *chunks,
    )


async def ask_user(
    ctx: RunContext[ChatDeps],
    payload: AskUserArgs,
) -> None:
    """Ask the user a clarifying question with clickable options when the request
    is ambiguous. The turn ends until the user answers."""
    result = {
        "question": payload.question,
        "options": [o.model_dump() for o in payload.options],
        "allow_multiple": payload.allow_multiple,
        "response_language": payload.response_language,
    }
    ctx.deps.pending_question = result
    ctx.deps.pending_question_id = ctx.tool_call_id
    # A user-facing question is an external tool call: the frontend supplies
    # its result on the next turn. Deferring here ends this run instead of
    # asking the model for another step (where it could repeat the question).
    raise CallDeferred(metadata={"pending_question": result})


async def render_data(
    ctx: RunContext[ChatDeps],  # noqa: ARG001
    payload: RenderDataArgs,
) -> ToolReturn:
    """Render a model-populated UI component. Use `data_table` for
    {title?, columns: [string], rows: [[string|number]]} and `stat_cards` for
    {items: [{label, value, sub?}]}."""
    return ToolReturn(
        return_value="UI component rendered.",
        metadata=[DataChunk(type=f"data-{payload.component}", data=payload.data)],
    )


# --------------------------------------------------------------------------- #
# Toolset
# --------------------------------------------------------------------------- #
# Write/destructive tools don't execute during the run; they emit a
# ``data-pending-confirmation`` DataChunk (see ``_defer_for_confirmation``) and
# the user confirms via ``POST /conversations/{id}/confirm`` which runs
# ``execute_tool``. No v6 approval gating (the v6 protocol can't carry edited
# args back).


# Chat-only tools; the rest come from the shared registry (``agent_tools``).
_CHAT_ONLY_TOOLS = (submit_feedback, ask_user, render_data)


def build_toolset() -> FunctionToolset[ChatDeps]:
    """Build the toolset for a run. Every tier gets the same tools."""
    ts: FunctionToolset[ChatDeps] = FunctionToolset()
    for tool in agent_tools(_defer_for_confirmation):
        ts.add_function(
            tool.function,
            takes_ctx=True,
            name=tool.name,
            description=tool.description,
            prepare=tool.prepare,
        )
    for fn in _CHAT_ONLY_TOOLS:
        ts.add_function(fn, takes_ctx=True)
    if settings.TAVILY_API_KEY:
        ts.add_function(search_web, takes_ctx=True)
    return ts


# --------------------------------------------------------------------------- #
# Observability hooks
# --------------------------------------------------------------------------- #


def _args_as_dict(args: Any) -> dict[str, Any] | None:
    if args is None:
        return None
    dump = getattr(args, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")  # type: ignore[no-any-return]
        except Exception:
            pass
    try:
        encoded = jsonable_encoder(args)
        return encoded if isinstance(encoded, dict) else {"value": encoded}
    except Exception:
        return None


async def _obs_tool_execute(
    ctx: RunContext[ChatDeps],
    *,
    call: Any,
    tool_def: ToolDefinition,  # noqa: ARG001
    args: Any,
    handler: Any,
) -> Any:
    started = time.perf_counter()
    try:
        result = await handler(args)
    except _TOOL_ERRORS as e:
        duration_ms = int((time.perf_counter() - started) * 1000)
        await record_tool_call(
            turn_id=ctx.deps.turn_id,
            round_index=ctx.run_step,
            tool_name=call.tool_name,
            arguments=_args_as_dict(args),
            result=None,
            status="failed",
            duration_ms=duration_ms,
            error=str(e),
        )
        return f"Error: {e}"
    except Exception as e:
        duration_ms = int((time.perf_counter() - started) * 1000)
        await record_tool_call(
            turn_id=ctx.deps.turn_id,
            round_index=ctx.run_step,
            tool_name=call.tool_name,
            arguments=_args_as_dict(args),
            result=None,
            status="failed",
            duration_ms=duration_ms,
            error=str(e),
        )
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    await record_tool_call(
        turn_id=ctx.deps.turn_id,
        round_index=ctx.run_step,
        tool_name=call.tool_name,
        arguments=_args_as_dict(args),
        result={"ok": True, "preview": str(result)[:500]},
        status="completed",
        duration_ms=duration_ms,
    )
    return result


async def _obs_after_model(
    ctx: RunContext[ChatDeps],
    *,
    request_context: Any,  # noqa: ARG001
    response: Any,
) -> Any:
    usage = response.usage
    await record_llm_call(
        turn_id=ctx.deps.turn_id,
        round_index=ctx.run_step,
        model=response.model_name or chat_model_for_tier(ctx.deps.tier),
        prompt_tokens=usage.input_tokens,
        completion_tokens=usage.output_tokens,
        total_tokens=usage.input_tokens + usage.output_tokens,
        cached_tokens=usage.cache_read_tokens,
        cost_usd=extract_cost_usd(response),
        finish_reason=response.finish_reason,
        had_tool_calls=bool(response.tool_calls),
    )
    return response


def build_obs_hooks() -> Hooks:
    return Hooks(
        tool_execute=_obs_tool_execute,
        after_model_request=_obs_after_model,
    )


async def _answer_when_budget_spent(
    ctx: RunContext[ChatDeps], request_context: ModelRequestContext
) -> ModelRequestContext:
    """Make the last affordable model call answer instead of calling tools."""
    if turn_budget_spent(ctx.usage):
        request_context.model_settings = merge_model_settings(
            request_context.model_settings, ModelSettings(tool_choice="none")
        )
    return request_context


def build_turn_budget() -> Hooks:
    return Hooks(before_model_request=_answer_when_budget_spent)
