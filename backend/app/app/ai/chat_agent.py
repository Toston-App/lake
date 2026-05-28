"""OpenRouter tool-calling agent for the finance chat API."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.chat_tools import (
    TOOL_DEFINITIONS,
    append_ui_rendered_reply_hint,
    build_pending_preview,
    build_system_prompt,
    execute_tool,
    format_confirmation_message,
    load_bootstrap_context,
    requires_confirmation,
)
from app.core.config import settings
from app.schemas.chat import ChatAction, PendingConfirmation, ToolStep, chat_action_detail
from app.utilities.redis import store_pending_action


@dataclass
class ChatTurnResult:
    reply: str
    actions: list[ChatAction] = field(default_factory=list)
    tool_steps: list[ToolStep] = field(default_factory=list)
    pending_confirmation: Optional[PendingConfirmation] = None
    new_messages: list[dict] = field(default_factory=list)


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


@dataclass
class _TurnState:
    messages: list[dict]
    actions: list[ChatAction]
    tool_steps: list[ToolStep]
    new_messages: list[dict]


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
            result, action_type = await execute_tool(
                tool_name, tool_args, db, user, confirmed=True
            )
            if action_type and result.get("success"):
                actions.append(
                    ChatAction(
                        type=action_type,
                        detail=chat_action_detail(result.get("data")),
                    )
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
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            stream=True,
            extra_body=self._extra_body(),
        )

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None

        async for chunk in stream:
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

        reply_text = "".join(content_parts)
        if tool_calls_acc:
            yield {
                "kind": "tool_calls",
                "content": reply_text or None,
                "tool_calls": _tool_calls_from_accumulator(tool_calls_acc),
            }
        else:
            yield {
                "kind": "text",
                "content": reply_text,
            }

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
    ) -> AsyncIterator[ChatStreamEvent]:
        assistant_msg = {
            "role": "assistant",
            "content": assistant_content,
            "tool_calls": tool_calls,
        }
        state.messages.append(assistant_msg)

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            try:
                tool_args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                tool_args = {}

            if requires_confirmation(tool_name, tool_args) and not confirmed_action:
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
                yield ChatStreamEvent(
                    type="done",
                    result=ChatTurnResult(
                        reply=message,
                        actions=state.actions,
                        tool_steps=state.tool_steps,
                        pending_confirmation=PendingConfirmation(
                            action_id=action_id,
                            tool_name=tool_name,
                            preview=preview,
                            message=message,
                        ),
                        new_messages=state.new_messages,
                    ),
                )
                return

            result, action_type = await execute_tool(
                tool_name, tool_args, db, user, confirmed=bool(confirmed_action)
            )
            if action_type and result.get("success"):
                state.actions.append(
                    ChatAction(
                        type=action_type,
                        detail=chat_action_detail(result.get("data")),
                    )
                )

            step = ToolStep(
                tool_name=tool_name,
                status="completed" if result.get("success") else "failed",
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
    ) -> AsyncIterator[ChatStreamEvent]:
        state = await self._prepare_turn(
            db, user, user_message, history, confirmed_action=confirmed_action
        )

        for _ in range(self.max_rounds):
            round_outcome: dict | None = None
            async for item in self._completion_round(state, emit_tokens=True):
                if isinstance(item, ChatStreamEvent):
                    yield item
                else:
                    round_outcome = item

            assert round_outcome is not None

            if round_outcome["kind"] == "text":
                reply = round_outcome["content"]
                state.new_messages.append({"role": "assistant", "content": reply})
                yield ChatStreamEvent(
                    type="done",
                    result=ChatTurnResult(
                        reply=reply,
                        actions=state.actions,
                        tool_steps=state.tool_steps,
                        new_messages=state.new_messages,
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
                tool_steps=state.tool_steps,
                new_messages=state.new_messages,
            ),
        )

    async def run_turn(
        self,
        db: AsyncSession,
        user: models.User,
        user_message: str,
        history: list[dict],
        *,
        conversation_id: str,
        confirmed_action: Optional[dict] = None,
    ) -> ChatTurnResult:
        result: ChatTurnResult | None = None
        async for event in self.stream_turn(
            db,
            user,
            user_message,
            history,
            conversation_id=conversation_id,
            confirmed_action=confirmed_action,
        ):
            if event.type == "done" and event.result is not None:
                result = event.result
        assert result is not None
        return result
