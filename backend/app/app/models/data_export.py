import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base

if TYPE_CHECKING:
    from .user import User


class DataExportStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    expired = "expired"


class DataExport(Base):
    __tablename__ = "data_export"
    __table_args__ = (
        Index(
            "uq_data_export_owner_inflight",
            "owner_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: DataExportStatus = Column(
        Enum(DataExportStatus, name="dataexportstatus", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=DataExportStatus.pending,
        index=True,
    )
    progress_pct: int = Column(Integer, nullable=False, default=0)
    progress_step: str | None = Column(String, nullable=True)
    object_key: str | None = Column(String, nullable=True)
    filename: str = Column(String, nullable=False)
    content_sha256: str | None = Column(String, nullable=True)
    byte_size: int | None = Column(Integer, nullable=True)
    error: str | None = Column(String, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    owner: "User" = relationship("User", back_populates="data_exports")
