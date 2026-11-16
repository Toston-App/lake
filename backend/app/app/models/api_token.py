"""Personal access tokens: static bearer credentials for MCP clients without OAuth."""

from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User  # noqa: F401


class ApiToken(Base):
    __tablename__ = "api_token"

    id: int = Column(Integer, primary_key=True, index=True)
    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: str = Column(String(100), nullable=False)
    # SHA-256 hex of the whole token; the plaintext is never stored.
    token_hash: str = Column(String(64), nullable=False, unique=True, index=True)
    # The first characters of the token, so the user can tell tokens apart.
    prefix: str = Column(String(12), nullable=False)
    # "read" or "write".
    scope: str = Column(String(10), nullable=False, default="write")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", back_populates="api_tokens")
