"""Postgres observability tables for the chat agent.

These tables capture per-turn, per-LLM-round, and per-tool-call telemetry so
the team can see what users generate, token/cost consumption, tool usage, and
errors -- the foundation for an observability dashboard.

They are written fire-and-forget from the agent loop and never gate user
facing behavior (see app.utilities.observability). conversation_id is stored
as a loose indexed string (no FK) so a turn can always be recorded even if the
conversation metadata upsert raced or failed.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db.base_class import Base


class ChatObsConversation(Base):
    __tablename__ = "chat_obs_conversations"

    id: str = Column(String, primary_key=True)
    user_id: int = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    title: str = Column(String, nullable=True)
    message_count: int = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatObsTurn(Base):
    __tablename__ = "chat_obs_turns"

    id: int = Column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: str = Column(String, nullable=False, index=True)
    user_id: int = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    trace_id: str = Column(String, nullable=True, index=True)
    request_id: str = Column(String, nullable=True, index=True)
    model: str = Column(String, nullable=True)
    provider: str = Column(String, nullable=False, default="openrouter")
    user_message: str = Column(Text, nullable=True)
    assistant_reply: str = Column(Text, nullable=True)
    # in_progress | ok | error | max_rounds | pending_confirmation | pending_question
    status: str = Column(String, nullable=False, default="ok", index=True)
    error: str = Column(Text, nullable=True)
    duration_ms: int = Column(Integer, nullable=True)
    rounds_count: int = Column(Integer, nullable=False, default=0)
    actions_count: int = Column(Integer, nullable=False, default=0)
    pending_confirmation: bool = Column(Boolean, nullable=False, default=False)
    pending_question: bool = Column(Boolean, nullable=False, default=False)
    total_prompt_tokens: int = Column(Integer, nullable=True)
    total_completion_tokens: int = Column(Integer, nullable=True)
    total_tokens: int = Column(Integer, nullable=True)
    total_cost_usd = Column(Numeric(12, 6), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)


class ChatObsLLMCall(Base):
    __tablename__ = "chat_obs_llm_calls"

    id: int = Column(BigInteger, primary_key=True, autoincrement=True)
    turn_id: int = Column(
        BigInteger,
        ForeignKey("chat_obs_turns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    round_index: int = Column(Integer, nullable=False, default=0)
    model: str = Column(String, nullable=True)
    provider: str = Column(String, nullable=False, default="openrouter")
    prompt_tokens: int = Column(Integer, nullable=True)
    completion_tokens: int = Column(Integer, nullable=True)
    total_tokens: int = Column(Integer, nullable=True)
    cached_tokens: int = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(12, 6), nullable=True)
    duration_ms: int = Column(Integer, nullable=True)
    finish_reason: str = Column(String, nullable=True)
    had_tool_calls: bool = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ChatObsToolCall(Base):
    __tablename__ = "chat_obs_tool_calls"

    id: int = Column(BigInteger, primary_key=True, autoincrement=True)
    turn_id: int = Column(
        BigInteger,
        ForeignKey("chat_obs_turns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    round_index: int = Column(Integer, nullable=False, default=0)
    tool_name: str = Column(String, nullable=False, index=True)
    arguments = Column(JSONB, nullable=True)
    result = Column(JSONB, nullable=True)
    # completed | failed | pending_confirmation
    status: str = Column(String, nullable=False, default="completed", index=True)
    duration_ms: int = Column(Integer, nullable=True)
    error: str = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ChatModelPricing(Base):
    __tablename__ = "chat_model_pricing"

    model: str = Column(String, primary_key=True)
    provider: str = Column(String, nullable=False, default="openrouter")
    input_price_per_1m = Column(Numeric(10, 6), nullable=False, default=0)
    output_price_per_1m = Column(Numeric(10, 6), nullable=False, default=0)
    cached_input_price_per_1m = Column(Numeric(10, 6), nullable=True)
    effective_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
