from typing import Optional

from pydantic import BaseModel


# Shared properties
class FeedbackBase(BaseModel):
    message: str
    sentiment: Optional[str] = None


# Properties to receive via API on creation
class FeedbackCreate(FeedbackBase):
    pass


# Properties to receive via API on update
class FeedbackUpdate(FeedbackBase):
    pass


class FeedbackInDBBase(FeedbackBase):
    id: int
    owner_id: int

    class Config:
        orm_mode = True


# Additional properties to return via API
class Feedback(FeedbackInDBBase):
    pass
