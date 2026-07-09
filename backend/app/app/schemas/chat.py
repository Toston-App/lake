from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: Optional[str] = None


def chat_action_detail(data: Any) -> Optional[dict[str, Any]]:
    """Normalize tool result data for ChatAction (detail must be a dict)."""
    if data is None:
        return None
    if isinstance(data, dict):
        return data
    return {"items": data}


class ChatAction(BaseModel):
    type: str
    detail: Optional[dict[str, Any]] = None


class RenderDirective(BaseModel):
    component: str
    detail: Optional[dict[str, Any]] = None


class ToolStep(BaseModel):
    tool_name: str
    status: str  # completed | failed | pending_confirmation
    label: Optional[str] = None


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
    tool_steps: list[ToolStep] = []
    pending_confirmation: Optional[PendingConfirmation] = None
    pending_question: Optional[PendingQuestion] = None
    # Vercel AI UI message (id/role/parts) for the new @ai-sdk/react frontend.
    # The frontend appends this to `useChat` messages via setMessages after a
    # successful /confirm. Absent for legacy clients (they use reply/actions).
    ui_message: Optional[dict[str, Any]] = None


class ChatConfirmRequest(BaseModel):
    action_id: str
    tool_args: Optional[dict[str, Any]] = None


class ConversationMessage(BaseModel):
    role: str
    content: str
    created_at: Optional[datetime] = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime
    last_message_preview: Optional[str] = None


class ConversationDetail(BaseModel):
    id: str
    title: str
    messages: list[ConversationMessage]
    # Vercel AI UI messages (id/role/parts) when the conversation was persisted
    # by the Pydantic AI + VercelAIAdapter flow. Present only for conversations
    # saved via POST /chat; absent for legacy conversations. The new frontend
    # seeds `useChat` from this when present and ignores `messages`.
    ui_messages: Optional[list[dict[str, Any]]] = None
    updated_at: datetime
