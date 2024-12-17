from sqlalchemy import Column, ForeignKey, Integer, String, Float, Date, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy import Enum as SQLAlchemyEnum

from app.db.base_class import Base

from enum import Enum as PyEnum


class ImportService(str, PyEnum):
    CSV = "csv"
    BLUECOINS = "bluecoins"

class Import(Base):
    id: int = Column(Integer, primary_key=True, index=True, nullable=False, unique=True)
    date: Date = Column(DateTime(timezone=True), onupdate=func.now())
    owner_id: int = Column(Integer, ForeignKey("user.id"))
    created_at: Date = Column(DateTime(timezone=True), server_default=func.now())
    updated_at: Date = Column(DateTime(timezone=True), onupdate=func.now())
    ended_at: Date = Column(DateTime(timezone=True))
    service: ImportService = Column(SQLAlchemyEnum(ImportService, name='importservice', create_constraint=True, native_enum=True), nullable=False)
    file_content: str = Column(String, nullable=False)
    file_size: int = Column(Integer)
    total_transactions_imported: int = Column(Integer)
    expenses_imported: int = Column(Integer)
    incomes_imported: int = Column(Integer)
    accounts_created: int = Column(Integer)
    sites_created: int = Column(Integer)
    unmatched_categories: int = Column(Integer)
    total_rows_processed: int = Column(Integer)
    owner = relationship("User", back_populates="imports")
    accounts = relationship("Account", back_populates="import_source", cascade="all, delete-orphan")
