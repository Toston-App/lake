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


class ChatStreamMeta(BaseModel):
    conversation_id: str


class ChatStreamToken(BaseModel):
    delta: str


class ChatStreamError(BaseModel):
    detail: str


class ChatStreamEvent(BaseModel):
    """SSE payload helper; event name is the `type` field value."""

    type: str  # meta | tool_step | token | done | error
    conversation_id: Optional[str] = None
    delta: Optional[str] = None
    tool_step: Optional[ToolStep] = None
    detail: Optional[str] = None
    response: Optional[ChatMessageResponse] = None


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
    updated_at: datetime
