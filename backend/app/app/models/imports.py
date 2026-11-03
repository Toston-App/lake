from enum import Enum as PyEnum

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base_class import Base


class ImportService(str, PyEnum):
    # BLUECOINS is kept only because historical rows reference it. Every new
    # import uses the generic CSV/XLSX pipeline.
    CSV = "csv"
    BLUECOINS = "bluecoins"


class ImportStatus(str, PyEnum):
    draft = "draft"
    completed = "completed"
    failed = "failed"
    reverted = "reverted"


class Import(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    date: Date = Column(DateTime(timezone=True), onupdate=func.now())
    owner_id: int = Column(Integer, ForeignKey("user.id"), index=True)
    created_at: Date = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: Date = Column(DateTime(timezone=True), onupdate=func.now())
    ended_at: Date = Column(DateTime(timezone=True))
    service: ImportService = Column(
        SQLAlchemyEnum(
            ImportService,
            name="importservice",
            create_constraint=True,
            native_enum=True,
        ),
        nullable=False,
    )
    status: ImportStatus = Column(
        SQLAlchemyEnum(
            ImportStatus,
            name="importstatus",
            create_constraint=True,
            native_enum=True,
        ),
        nullable=False,
        default=ImportStatus.completed,
        server_default=ImportStatus.completed.name,
        index=True,
    )
    # Deprecated: the uploaded file is never stored anymore.
    file_content: str = Column(String, nullable=True)
    file_size: int = Column(Integer)
    filename: str = Column(String(255))
    # SHA-256 of the uploaded bytes, used to warn about re-importing a file.
    file_hash: str = Column(String(64), index=True)
    header_fingerprint: str = Column(String(64))
    # Column mapping, parse options and value decisions used for the import.
    mapping: dict = Column(JSONB)
    # Encrypted, compressed parsed rows while the import is a draft. Cleared on
    # commit, cancel or expiry.
    staged_payload: bytes = Column(LargeBinary, nullable=True)
    expires_at = Column(DateTime(timezone=True))
    reverted_at = Column(DateTime(timezone=True))
    total_transactions_imported: int = Column(Integer)
    expenses_imported: int = Column(Integer)
    incomes_imported: int = Column(Integer)
    transfers_imported: int = Column(Integer)
    accounts_created: int = Column(Integer)
    sites_created: int = Column(Integer)
    categories_created: int = Column(Integer)
    subcategories_created: int = Column(Integer)
    duplicates_skipped: int = Column(Integer)
    rows_failed: int = Column(Integer)
    unmatched_categories: int = Column(Integer)
    total_rows_processed: int = Column(Integer)
    owner = relationship("User", back_populates="imports")
    accounts = relationship(
        "Account", back_populates="import_source", cascade="all, delete-orphan"
    )


class ImportProfile(Base):
    """A saved column mapping + value mapping, reused for files with the same headers."""

    __tablename__ = "import_profile"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "header_fingerprint", name="uq_import_profile_owner_headers"
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    owner_id: int = Column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: str = Column(String(120), nullable=False)
    header_fingerprint: str = Column(String(64), nullable=False)
    headers: list = Column(JSONB, nullable=False)
    column_mapping: dict = Column(JSONB, nullable=False)
    options: dict = Column(JSONB, nullable=False)
    value_mappings: dict = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_used_at = Column(DateTime(timezone=True))
