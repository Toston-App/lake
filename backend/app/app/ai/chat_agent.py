"""OpenRouter tool-calling agent for the finance chat API."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.ai.chat_tools import (
    TOOL_DEFINITIONS,
    build_pending_preview,
    build_system_prompt,
    execute_tool,
    format_confirmation_message,
    load_bootstrap_context,
    requires_confirmation,
)
from app.core.config import settings
from app.schemas.chat import ChatAction, PendingConfirmation, ToolStep
from app.utilities.redis import store_pending_action


@dataclass
class ChatTurnResult:
    reply: str
    actions: list[ChatAction] = field(default_factory=list)
    tool_steps: list[ToolStep] = field(default_factory=list)
    pending_confirmation: Optional[PendingConfirmation] = None
    new_messages: list[dict] = field(default_factory=list)


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


class ChatAgent:
    def __init__(self, client: AsyncOpenAI | None = None):
        self.client = client or create_openrouter_client()
        self.model = settings.CHAT_MODEL or settings.OPENROUTER_MODEL
        self.fallback_models = _parse_fallback_models()
        self.max_rounds = settings.CHAT_MAX_TOOL_ROUNDS

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
        new_messages = [
            {"role": "user", "content": user_message},
        ]

        if confirmed_action:
            tool_name = confirmed_action["tool_name"]
            tool_args = confirmed_action["tool_args"]
            result, action_type = await execute_tool(
                tool_name, tool_args, db, user, confirmed=True
            )
            if action_type and result.get("success"):
                actions.append(
                    ChatAction(type=action_type, detail=result.get("data"))
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

        extra_body = None
        if self.fallback_models:
            extra_body = {"models": self.fallback_models}

        for _ in range(self.max_rounds):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                extra_body=extra_body,
            )
            choice = response.choices[0].message

            if not choice.tool_calls:
                reply = choice.content or ""
                new_messages.append({"role": "assistant", "content": reply})
                return ChatTurnResult(
                    reply=reply,
                    actions=actions,
                    tool_steps=tool_steps,
                    new_messages=new_messages,
                )

            assistant_msg = {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in choice.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments or "{}")
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
                    tool_steps.append(
                        ToolStep(
                            tool_name=tool_name,
                            status="pending_confirmation",
                            label=_tool_label(tool_name, tool_args),
                        )
                    )
                    new_messages.append(
                        {"role": "assistant", "content": message}
                    )
                    return ChatTurnResult(
                        reply=message,
                        actions=actions,
                        tool_steps=tool_steps,
                        pending_confirmation=PendingConfirmation(
                            action_id=action_id,
                            tool_name=tool_name,
                            preview=preview,
                            message=message,
                        ),
                        new_messages=new_messages,
                    )

                result, action_type = await execute_tool(
                    tool_name, tool_args, db, user, confirmed=bool(confirmed_action)
                )
                if action_type and result.get("success"):
                    actions.append(
                        ChatAction(type=action_type, detail=result.get("data"))
                    )

                tool_steps.append(
                    ToolStep(
                        tool_name=tool_name,
                        status="completed" if result.get("success") else "failed",
                        label=_tool_label(tool_name, tool_args),
                    )
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        reply = (
            "I reached the maximum number of steps for this request. "
            "Please try a simpler question or break it into smaller parts."
        )
        new_messages.append({"role": "assistant", "content": reply})
        return ChatTurnResult(
            reply=reply, actions=actions, tool_steps=tool_steps, new_messages=new_messages
        )
