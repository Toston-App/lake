from datetime import datetime

from pydantic import BaseModel


# Shared properties
class PlaceBase(BaseModel):
    name: str | None = None
    is_online: bool | None = None


# Properties to receive on place creation
class PlaceCreate(PlaceBase):
    name: str
    import_id: int | None = None


# Properties to receive on place update
class PlaceUpdate(PlaceBase):
    updated_at: datetime | None = None


# Properties shared by models stored in DB
class PlaceInDBBase(PlaceBase):
    id: int
    owner_id: int
    import_id: int | None = None

    class Config:
        orm_mode = True


# Properties to return to client
class Place(PlaceInDBBase):
    pass


# Properties properties stored in DB
class PlaceInDB(PlaceInDBBase):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DeletionResponse(BaseModel):
    message: str
