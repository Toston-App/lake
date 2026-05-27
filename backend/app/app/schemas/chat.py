from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: Optional[str] = None


class ChatAction(BaseModel):
    type: str
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


class ChatMessageResponse(BaseModel):
    conversation_id: str
    reply: str
    actions: list[ChatAction] = []
    tool_steps: list[ToolStep] = []
    pending_confirmation: Optional[PendingConfirmation] = None


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
