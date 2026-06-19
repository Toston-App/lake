"""Pydantic schemas for the chat observability admin dashboard."""

from typing import Any, Optional

from pydantic import BaseModel


class ObservabilitySummary(BaseModel):
    turns: int
    errors: int
    error_rate: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: Optional[float] = None
    avg_cost_per_turn: Optional[float] = None
    avg_duration_ms: Optional[float] = None
    pending_confirmations: int
    pending_questions: int
    unique_users: int
    unique_conversations: int


class TimeseriesPoint(BaseModel):
    bucket: Optional[str] = None
    turns: int
    tokens: int
    cost: Optional[float] = None


class ToolStat(BaseModel):
    tool_name: str
    calls: int
    failed: int
    error_rate: float
    avg_duration_ms: Optional[float] = None


class ModelStat(BaseModel):
    model: Optional[str] = None
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: Optional[float] = None
    avg_duration_ms: Optional[float] = None


class TurnRow(BaseModel):
    id: int
    conversation_id: str
    user_id: int
    user_email: Optional[str] = None
    model: Optional[str] = None
    status: str
    duration_ms: Optional[int] = None
    rounds_count: int
    actions_count: int
    total_tokens: Optional[int] = None
    total_cost_usd: Optional[float] = None
    pending_confirmation: bool
    pending_question: bool
    error: Optional[str] = None
    user_message: Optional[str] = None
    started_at: Optional[str] = None


class LLMCallDetail(BaseModel):
    id: int
    round_index: int
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    finish_reason: Optional[str] = None
    had_tool_calls: bool
    created_at: Optional[str] = None


class ToolCallDetail(BaseModel):
    id: int
    round_index: int
    tool_name: str
    arguments: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    status: str
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    created_at: Optional[str] = None


class TurnDetail(TurnRow):
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    provider: str = "openrouter"
    assistant_reply: Optional[str] = None
    total_prompt_tokens: Optional[int] = None
    total_completion_tokens: Optional[int] = None
    ended_at: Optional[str] = None
    llm_calls: list[LLMCallDetail] = []
    tool_calls: list[ToolCallDetail] = []


class TurnsListResponse(BaseModel):
    items: list[TurnRow]
    count: int
