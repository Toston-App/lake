from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# Shared properties
class ImportBase(BaseModel):
    date: str
    service: str
    file_content: str
    file_size: Optional[int] = None
    total_transactions_imported: Optional[int] = None
    expenses_imported: Optional[int] = None
    incomes_imported: Optional[int] = None
    accounts_created: Optional[int] = None
    unmatched_categories: Optional[int] = None
    total_rows_processed: Optional[int] = None
    ended_at: Optional[datetime] = None


# Properties to receive on Import creation
class ImportCreate(ImportBase):
    pass

# Properties to receive on Import update
class ImportUpdate(ImportBase):
    updated_at: Optional[datetime] = None

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
    pass

class DeletionResponse(BaseModel):
    message: str