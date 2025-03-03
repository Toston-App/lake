from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# Shared properties
class PlaceBase(BaseModel):
    name: Optional[str] = None
    is_online: Optional[bool] = None


# Properties to receive on place creation
class PlaceCreate(PlaceBase):
    name: str
    import_id: Optional[int] = None


# Properties to receive on place update
class PlaceUpdate(PlaceBase):
    updated_at: Optional[datetime] = None


# Properties shared by models stored in DB
class PlaceInDBBase(PlaceBase):
    id: int
    owner_id: int
    import_id: Optional[int] = None

    class Config:
        orm_mode = True


# Properties to return to client
class Place(PlaceInDBBase):
    pass


# Properties properties stored in DB
class PlaceInDB(PlaceInDBBase):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class DeletionResponse(BaseModel):
    message: str