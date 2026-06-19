"""OpenRouter tool-calling agent for the finance chat API."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.chat_tools import (
    append_ui_rendered_reply_hint,
    build_pending_preview,
    build_system_prompt,
    execute_tool,
    format_confirmation_message,
    get_tool_definitions,
    load_bootstrap_context,
    requires_confirmation,
)
from app.ai.render_components import (
    component_for_action_type,
    is_model_data_component,
    is_valid_component,
    resolve_render_detail,
)
from app.core.config import settings
from app.schemas.chat import (
    ChatAction,
    PendingConfirmation,
    PendingQuestion,
    QuestionOption,
    RenderDirective,
    ToolStep,
    chat_action_detail,
)
from app.utilities.observability import record_llm_call, record_tool_call
from app.utilities.redis import store_pending_action


@dataclass
class ChatTurnResult:
    reply: str
    actions: list[ChatAction] = field(default_factory=list)
    render: list[RenderDirective] = field(default_factory=list)
    tool_steps: list[ToolStep] = field(default_factory=list)
    pending_confirmation: Optional[PendingConfirmation] = None
    pending_question: Optional[PendingQuestion] = None
    new_messages: list[dict] = field(default_factory=list)
    # Observability metadata (not exposed in the API response).
    rounds_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    max_rounds_reached: bool = False


@dataclass
class ChatStreamEvent:
    type: str  # token | tool_step | done
    delta: Optional[str] = None
    tool_step: Optional[ToolStep] = None
    result: Optional[ChatTurnResult] = None


def _tool_label(tool_name: str, tool_args: dict) -> str:
    labels = {
        "get_period_summary": "Fetching period summary",
        "get_period_charts": "Loading charts",
        "list_accounts": "Listing accounts",
        "search_transactions": "Searching transactions",
        "create_expense": "Creating expense",
        "create_income": "Creating income",
        "delete_expense": "Deleting expense",
        "delete_income": "Deleting income",
        "search_web": "Searching the web",
        "ask_user": "Asking a question",
    }
    base = labels.get(tool_name, tool_name.replace("_", " ").title())
    if tool_name in ("get_period_summary", "get_period_charts"):
        period = tool_args.get("date_filter_type")
        date = tool_args.get("date")
        if period and date:
            return f"{base} ({period} {date})"
    return base


def _parse_fallback_models() -> list[str] | None:
    if not settings.OPENROUTER_FALLBACK_MODELS:
        return None
    models_list = [
        m.strip()
        for m in settings.OPENROUTER_FALLBACK_MODELS.split(",")
        if m.strip()
    ]
    return models_list or None


def create_openrouter_client() -> AsyncOpenAI:
    headers = {}
    if settings.OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = settings.OPENROUTER_SITE_URL
    if settings.OPENROUTER_APP_NAME:
        headers["X-Title"] = settings.OPENROUTER_APP_NAME
    return AsyncOpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers=headers if headers else None,
    )


def _accumulate_tool_call_delta(
    acc: dict[int, dict[str, str]], tc_delta: Any
) -> None:
    idx = tc_delta.index
    if idx not in acc:
        acc[idx] = {"id": "", "name": "", "arguments": ""}
    if tc_delta.id:
        acc[idx]["id"] = tc_delta.id
    if tc_delta.function:
        if tc_delta.function.name:
            acc[idx]["name"] = tc_delta.function.name
        if tc_delta.function.arguments:
            acc[idx]["arguments"] += tc_delta.function.arguments


def _tool_calls_from_accumulator(acc: dict[int, dict[str, str]]) -> list[dict]:
    return [
        {
            "id": entry["id"],
            "type": "function",
            "function": {
                "name": entry["name"],
                "arguments": entry["arguments"],
            },
        }
        for _, entry in sorted(acc.items())
    ]


def _extract_usage(usage_obj: Any) -> dict[str, Any]:
    """Normalize an OpenAI/OpenRouter usage object into a flat dict.

    OpenRouter additionally reports ``usage.cost`` (USD) as an extra field;
    the OpenAI SDK exposes extras via attribute access / model_dump.
    """
    prompt_tokens = getattr(usage_obj, "prompt_tokens", None)
    completion_tokens = getattr(usage_obj, "completion_tokens", None)
    total_tokens = getattr(usage_obj, "total_tokens", None)

    cached_tokens: Optional[int] = None
    details = getattr(usage_obj, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if cached is not None:
            cached_tokens = int(cached)

    cost_usd: Optional[float] = None
    cost = getattr(usage_obj, "cost", None)
    if cost is None:
        extra = getattr(usage_obj, "model_extra", None)
        if isinstance(extra, dict):
            cost = extra.get("cost")
    if cost is not None:
        try:
            cost_usd = float(cost)
        except (TypeError, ValueError):
            cost_usd = None

    return {
        "prompt_tokens": int(prompt_tokens) if prompt_tokens is not None else None,
        "completion_tokens": int(completion_tokens)
        if completion_tokens is not None
        else None,
        "total_tokens": int(total_tokens) if total_tokens is not None else None,
        "cached_tokens": cached_tokens,
        "cost_usd": cost_usd,
    }


@dataclass
class _TurnState:
    messages: list[dict]
    actions: list[ChatAction]
    tool_steps: list[ToolStep]
    new_messages: list[dict]
    render: list[RenderDirective] = field(default_factory=list)
    turn_id: Optional[int] = None
    round_index: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0


class ChatAgent:
    def __init__(self, client: AsyncOpenAI | None = None):
        self.client = client or create_openrouter_client()
        self.model = settings.CHAT_MODEL or settings.OPENROUTER_MODEL
        self.fallback_models = _parse_fallback_models()
        self.max_rounds = settings.CHAT_MAX_TOOL_ROUNDS

    def _extra_body(self) -> dict[str, Any] | None:
        if self.fallback_models:
            return {"models": self.fallback_models}
        return None

    async def _prepare_turn(
        self,
        db: AsyncSession,
        user: models.User,
        user_message: str,
        history: list[dict],
        *,
        confirmed_action: Optional[dict] = None,
        turn_id: Optional[int] = None,
    ) -> _TurnState:
        bootstrap = await load_bootstrap_context(db, user)
        system_prompt = build_system_prompt(bootstrap)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        for msg in history:
            role = msg.get("role")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": msg.get("content", "")})

        messages.append({"role": "user", "content": user_message})

        actions: list[ChatAction] = []
        tool_steps: list[ToolStep] = []
        new_messages = [{"role": "user", "content": user_message}]

        if confirmed_action:
            tool_name = confirmed_action["tool_name"]
            tool_args = confirmed_action["tool_args"]
            started = time.perf_counter()
            result, action_type = await execute_tool(
                tool_name, tool_args, db, user, confirmed=True
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            if action_type and result.get("success"):
                actions.append(
                    ChatAction(
                        type=action_type,
                        detail=chat_action_detail(result.get("data")),
                    )
                )
            await record_tool_call(
                turn_id=turn_id,
                round_index=0,
                tool_name=tool_name,
                arguments=tool_args,
                result=result,
                status="completed" if result.get("success") else "failed",
                duration_ms=duration_ms,
                error=None if result.get("success") else result.get("error"),
            )
            tool_summary = json.dumps(result, ensure_ascii=False)
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "confirm",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": "confirm",
                    "content": tool_summary,
                }
            )

        return _TurnState(
            messages=messages,
            actions=actions,
            tool_steps=tool_steps,
            new_messages=new_messages,
            turn_id=turn_id,
        )

    async def _completion_round(
        self,
        state: _TurnState,
        *,
        emit_tokens: bool,
    ) -> AsyncIterator[ChatStreamEvent | dict]:
        """Yield token events, then a sentinel dict with round outcome."""
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=state.messages,
            tools=get_tool_definitions(),
            tool_choice="auto",
            stream=True,
            stream_options={"include_usage": True},
            extra_body=self._extra_body(),
        )

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage_obj: Any = None
        started = time.perf_counter()

        async for chunk in stream:
            if chunk.usage:
                usage_obj = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta

            if delta.content:
                content_parts.append(delta.content)
                if emit_tokens:
                    yield ChatStreamEvent(type="token", delta=delta.content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    _accumulate_tool_call_delta(tool_calls_acc, tc_delta)

        usage = _extract_usage(usage_obj) if usage_obj is not None else None
        duration_ms = int((time.perf_counter() - started) * 1000)
        reply_text = "".join(content_parts)
        had_tool_calls = bool(tool_calls_acc)
        if tool_calls_acc:
            yield {
                "kind": "tool_calls",
                "content": reply_text or None,
                "tool_calls": _tool_calls_from_accumulator(tool_calls_acc),
                "usage": usage,
                "duration_ms": duration_ms,
                "model": self.model,
                "finish_reason": finish_reason,
                "had_tool_calls": had_tool_calls,
            }
        else:
            yield {
                "kind": "text",
                "content": reply_text,
                "usage": usage,
                "duration_ms": duration_ms,
                "model": self.model,
                "finish_reason": finish_reason,
                "had_tool_calls": had_tool_calls,
            }

    def _apply_render_ui(self, state: _TurnState, tool_args: dict) -> list[str]:
        """Resolve the model's render_ui selection into state.render.

        Returns the list of component ids that were accepted (valid and backed
        by data from this turn).
        """
        accepted: list[str] = []
        components = tool_args.get("components") or []
        for spec in components:
            if not isinstance(spec, dict):
                continue
            component_id = spec.get("component")
            if not component_id or not is_valid_component(component_id):
                continue
            if is_model_data_component(component_id):
                data = spec.get("data")
                if not isinstance(data, dict):
                    continue
                state.render.append(
                    RenderDirective(component=component_id, detail=data)
                )
                accepted.append(component_id)
                continue
            directive = resolve_render_detail(
                component_id,
                state.actions,
                from_action=spec.get("from_action"),
            )
            if directive is None:
                continue
            state.render.append(directive)
            accepted.append(component_id)
        return accepted

    def _finalize_render(self, state: _TurnState) -> list[RenderDirective]:
        """Return the turn's render plan, falling back to UI-capable actions.

        When the model explicitly selected components via render_ui, that plan is
        authoritative. Otherwise, derive a plan from the actions produced this
        turn so behavior is no worse than rendering every action.
        """
        if state.render:
            return state.render

        fallback: list[RenderDirective] = []
        for action in state.actions:
            component_id = component_for_action_type(action.type)
            if component_id is None:
                continue
            fallback.append(
                RenderDirective(component=component_id, detail=action.detail)
            )
        return fallback

    async def _handle_tool_calls(
        self,
        state: _TurnState,
        tool_calls: list[dict],
        assistant_content: str | None,
        *,
        db: AsyncSession,
        user: models.User,
        conversation_id: str,
        confirmed_action: Optional[dict],
        emit_steps: bool,
        round_index: int,
    ) -> AsyncIterator[ChatStreamEvent]:
        assistant_msg = {
            "role": "assistant",
            "content": assistant_content,
            "tool_calls": tool_calls,
        }
        state.messages.append(assistant_msg)

        def _obs_kwargs() -> dict[str, Any]:
            return {
                "rounds_count": state.round_index,
                "total_prompt_tokens": state.total_prompt_tokens,
                "total_completion_tokens": state.total_completion_tokens,
                "total_tokens": state.total_tokens,
                "total_cost_usd": state.total_cost_usd,
            }

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            try:
                tool_args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                tool_args = {}

            if tool_name == "render_ui":
                started = time.perf_counter()
                accepted = self._apply_render_ui(state, tool_args)
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(
                            {"success": True, "rendered": accepted},
                            ensure_ascii=False,
                        ),
                    }
                )
                await record_tool_call(
                    turn_id=state.turn_id,
                    round_index=round_index,
                    tool_name=tool_name,
                    arguments=tool_args,
                    result={"rendered": accepted},
                    status="completed",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                continue

            if tool_name == "ask_user":
                question = str(tool_args.get("question") or "").strip()
                raw_options = tool_args.get("options") or []
                allow_multiple = bool(tool_args.get("allow_multiple", False))
                options: list[QuestionOption] = []
                for opt in raw_options:
                    if not isinstance(opt, dict):
                        continue
                    opt_id = str(opt.get("id") or "").strip()
                    label = str(opt.get("label") or "").strip()
                    if opt_id and label:
                        options.append(QuestionOption(id=opt_id, label=label))
                if not question or len(options) < 2:
                    err = "ask_user requires a question and at least 2 options"
                    state.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(
                                {"success": False, "error": err},
                                ensure_ascii=False,
                            ),
                        }
                    )
                    await record_tool_call(
                        turn_id=state.turn_id,
                        round_index=round_index,
                        tool_name=tool_name,
                        arguments=tool_args,
                        result={"success": False},
                        status="failed",
                        error=err,
                    )
                    continue
                step = ToolStep(
                    tool_name=tool_name,
                    status="completed",
                    label=_tool_label(tool_name, tool_args),
                )
                state.tool_steps.append(step)
                if emit_steps:
                    yield ChatStreamEvent(type="tool_step", tool_step=step)
                state.new_messages.append(
                    {"role": "assistant", "content": question}
                )
                await record_tool_call(
                    turn_id=state.turn_id,
                    round_index=round_index,
                    tool_name=tool_name,
                    arguments=tool_args,
                    result={"question": question, "options_count": len(options)},
                    status="completed",
                )
                yield ChatStreamEvent(
                    type="done",
                    result=ChatTurnResult(
                        reply=question,
                        actions=state.actions,
                        render=self._finalize_render(state),
                        tool_steps=state.tool_steps,
                        pending_question=PendingQuestion(
                            question=question,
                            options=options,
                            allow_multiple=allow_multiple,
                        ),
                        new_messages=state.new_messages,
                        **_obs_kwargs(),
                    ),
                )
                return

            if requires_confirmation(tool_name, tool_args) and not confirmed_action:
                started = time.perf_counter()
                action_id = str(uuid.uuid4())
                preview = await build_pending_preview(
                    db, user, tool_name, tool_args
                )
                message = format_confirmation_message(tool_name, preview)
                await store_pending_action(
                    action_id=action_id,
                    user_id=user.id,
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    preview=preview,
                    turn_id=state.turn_id,
                )
                step = ToolStep(
                    tool_name=tool_name,
                    status="pending_confirmation",
                    label=_tool_label(tool_name, tool_args),
                )
                state.tool_steps.append(step)
                state.new_messages.append({"role": "assistant", "content": message})
                if emit_steps:
                    yield ChatStreamEvent(type="tool_step", tool_step=step)
                await record_tool_call(
                    turn_id=state.turn_id,
                    round_index=round_index,
                    tool_name=tool_name,
                    arguments=tool_args,
                    result=preview,
                    status="pending_confirmation",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                yield ChatStreamEvent(
                    type="done",
                    result=ChatTurnResult(
                        reply=message,
                        actions=state.actions,
                        render=self._finalize_render(state),
                        tool_steps=state.tool_steps,
                        pending_confirmation=PendingConfirmation(
                            action_id=action_id,
                            tool_name=tool_name,
                            preview=preview,
                            message=message,
                        ),
                        new_messages=state.new_messages,
                        **_obs_kwargs(),
                    ),
                )
                return

            started = time.perf_counter()
            result, action_type = await execute_tool(
                tool_name, tool_args, db, user, confirmed=bool(confirmed_action)
            )
            duration_ms = int((time.perf_counter() - started) * 1000)
            if action_type and result.get("success"):
                state.actions.append(
                    ChatAction(
                        type=action_type,
                        detail=chat_action_detail(result.get("data")),
                    )
                )

            success = bool(result.get("success"))
            step = ToolStep(
                tool_name=tool_name,
                status="completed" if success else "failed",
                label=_tool_label(tool_name, tool_args),
            )
            state.tool_steps.append(step)
            if emit_steps:
                yield ChatStreamEvent(type="tool_step", tool_step=step)

            state.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            await record_tool_call(
                turn_id=state.turn_id,
                round_index=round_index,
                tool_name=tool_name,
                arguments=tool_args,
                result=result,
                status="completed" if success else "failed",
                duration_ms=duration_ms,
                error=None if success else result.get("error"),
            )

        append_ui_rendered_reply_hint(
            state.messages, [a.type for a in state.actions]
        )

    async def stream_turn(
        self,
        db: AsyncSession,
        user: models.User,
        user_message: str,
        history: list[dict],
        *,
        conversation_id: str,
        confirmed_action: Optional[dict] = None,
        turn_id: Optional[int] = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        state = await self._prepare_turn(
            db,
            user,
            user_message,
            history,
            confirmed_action=confirmed_action,
            turn_id=turn_id,
        )

        for _ in range(self.max_rounds):
            round_idx = state.round_index
            round_outcome: dict | None = None
            async for item in self._completion_round(state, emit_tokens=True):
                if isinstance(item, ChatStreamEvent):
                    yield item
                else:
                    round_outcome = item

            assert round_outcome is not None
            state.round_index += 1
            await self._record_round_usage(state, round_outcome)

            if round_outcome["kind"] == "text":
                reply = round_outcome["content"]
                state.new_messages.append({"role": "assistant", "content": reply})
                yield ChatStreamEvent(
                    type="done",
                    result=ChatTurnResult(
                        reply=reply,
                        actions=state.actions,
                        render=self._finalize_render(state),
                        tool_steps=state.tool_steps,
                        new_messages=state.new_messages,
                        rounds_count=state.round_index,
                        total_prompt_tokens=state.total_prompt_tokens,
                        total_completion_tokens=state.total_completion_tokens,
                        total_tokens=state.total_tokens,
                        total_cost_usd=state.total_cost_usd,
                    ),
                )
                return

            async for event in self._handle_tool_calls(
                state,
                round_outcome["tool_calls"],
                round_outcome.get("content"),
                db=db,
                user=user,
                conversation_id=conversation_id,
                confirmed_action=confirmed_action,
                emit_steps=True,
                round_index=round_idx,
            ):
                yield event
                if event.type == "done":
                    return

        reply = (
            "I reached the maximum number of steps for this request. "
            "Please try a simpler question or break it into smaller parts."
        )
        state.new_messages.append({"role": "assistant", "content": reply})
        yield ChatStreamEvent(
            type="done",
            result=ChatTurnResult(
                reply=reply,
                actions=state.actions,
                render=self._finalize_render(state),
                tool_steps=state.tool_steps,
                new_messages=state.new_messages,
                rounds_count=state.round_index,
                total_prompt_tokens=state.total_prompt_tokens,
                total_completion_tokens=state.total_completion_tokens,
                total_tokens=state.total_tokens,
                total_cost_usd=state.total_cost_usd,
                max_rounds_reached=True,
            ),
        )

    async def _record_round_usage(self, state: _TurnState, outcome: dict) -> None:
        usage: Optional[dict] = outcome.get("usage")
        if state.turn_id is not None:
            await record_llm_call(
                turn_id=state.turn_id,
                round_index=state.round_index - 1,
                model=outcome.get("model") or self.model,
                prompt_tokens=usage.get("prompt_tokens") if usage else None,
                completion_tokens=usage.get("completion_tokens") if usage else None,
                total_tokens=usage.get("total_tokens") if usage else None,
                cached_tokens=usage.get("cached_tokens") if usage else None,
                cost_usd=usage.get("cost_usd") if usage else None,
                duration_ms=outcome.get("duration_ms"),
                finish_reason=outcome.get("finish_reason"),
                had_tool_calls=bool(outcome.get("had_tool_calls")),
            )
        if usage:
            if usage.get("prompt_tokens") is not None:
                state.total_prompt_tokens += int(usage["prompt_tokens"])
            if usage.get("completion_tokens") is not None:
                state.total_completion_tokens += int(usage["completion_tokens"])
            if usage.get("total_tokens") is not None:
                state.total_tokens += int(usage["total_tokens"])
            if usage.get("cost_usd") is not None:
                state.total_cost_usd += float(usage["cost_usd"])

    async def run_turn(
        self,
        db: AsyncSession,
        user: models.User,
        user_message: str,
        history: list[dict],
        *,
        conversation_id: str,
        confirmed_action: Optional[dict] = None,
        turn_id: Optional[int] = None,
    ) -> ChatTurnResult:
        result: ChatTurnResult | None = None
        async for event in self.stream_turn(
            db,
            user,
            user_message,
            history,
            conversation_id=conversation_id,
            confirmed_action=confirmed_action,
            turn_id=turn_id,
        ):
            if event.type == "done" and event.result is not None:
                result = event.result
        assert result is not None
        return result
