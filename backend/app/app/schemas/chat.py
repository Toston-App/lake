from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: str | None = None


def chat_action_detail(data: Any) -> dict[str, Any] | None:
    """Normalize tool result data for ChatAction (detail must be a dict)."""
    if data is None:
        return None
    if isinstance(data, dict):
        return data
    return {"items": data}


class ChatAction(BaseModel):
    type: str
    detail: dict[str, Any] | None = None


class RenderDirective(BaseModel):
    component: str
    detail: dict[str, Any] | None = None


class PendingConfirmation(BaseModel):
    action_id: str
    tool_name: str
    preview: dict[str, Any]
    message: str


class QuestionOption(BaseModel):
    id: str
    label: str


class PendingQuestion(BaseModel):
    question: str
    options: list[QuestionOption]
    allow_multiple: bool = False


class ChatMessageResponse(BaseModel):
    conversation_id: str
    reply: str
    actions: list[ChatAction] = []
    render: list[RenderDirective] = []
    pending_confirmation: PendingConfirmation | None = None
    pending_question: PendingQuestion | None = None
    # Vercel AI UI message (id/role/parts) for the new @ai-sdk/react frontend.
    # The frontend appends this to `useChat` messages via setMessages after a
    # successful /confirm. Absent for legacy clients (they use reply/actions).
    ui_message: dict[str, Any] | None = None


class ChatConfirmRequest(BaseModel):
    action_id: str
    tool_args: dict[str, Any] | None = None


class ConversationMessage(BaseModel):
    role: str
    content: str
    created_at: datetime | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime
    last_message_preview: str | None = None


class ConversationDetail(BaseModel):
    id: str
    title: str
    messages: list[ConversationMessage]
    # Vercel AI UI messages (id/role/parts) when the conversation was persisted
    # by the Pydantic AI + VercelAIAdapter flow. Present only for conversations
    # saved via POST /chat; absent for legacy conversations. The new frontend
    # seeds `useChat` from this when present and ignores `messages`.
    ui_messages: list[dict[str, Any]] | None = None
    updated_at: datetime
