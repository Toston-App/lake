from datetime import datetime

from pydantic import BaseModel

from app.models.imports import ImportService


# Shared properties
class ImportBase(BaseModel):
    service: ImportService | None = None
    file_content: str | None = None
    file_size: int | None = None
    total_transactions_imported: int | None = None
    expenses_imported: int | None = None
    incomes_imported: int | None = None
    accounts_created: int | None = None
    sites_created: int | None = None
    unmatched_categories: int | None = None
    total_rows_processed: int | None = None
    ended_at: datetime | None = None


# Properties to receive on Import creation
class ImportCreate(ImportBase):
    pass


# Properties to receive on Import update
class ImportUpdate(ImportBase):
    pass


# Properties shared by models stored in DB
class ImportInDBBase(ImportBase):
    id: int
    owner_id: int

    class Config:
        orm_mode = True


# Properties to return to client
class Import(ImportInDBBase):
    pass


# Properties properties stored in DB
class ImportInDB(ImportInDBBase):
    date: datetime | None = None


class DeletionResponse(BaseModel):
    message: str
