"""One row per MCP tool call: who called what, through which client, and how it went.

Arguments and results are not stored: they hold the user's financial data.
"""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.db.base_class import Base


class McpToolCall(Base):
    __tablename__ = "mcp_tool_call"
    __table_args__ = (
        Index("ix_mcp_tool_call_user_created_at", "user_id", "created_at"),
    )

    id: int = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    # "oauth:<client id>", "oauth" when Clerk names no client, or "pat:<token id>".
    client: str = Column(String(220), nullable=True)
    tool_name: str = Column(String(100), nullable=False)
    # completed | failed | confirmation_required | refused
    status: str = Column(String(30), nullable=False)
    # The pending action a gated call created, to match it with its confirm.
    action_id: str = Column(String(64), nullable=True)
    duration_ms: int = Column(Integer, nullable=True)
    # A short reason code for refused calls (e.g. read_only_token). Failed
    # calls store none: tool error messages can quote the user's data.
    error: str = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
