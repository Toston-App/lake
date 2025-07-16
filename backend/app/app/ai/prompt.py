import json
from enum import Enum
from pydantic import BaseModel
from typing import List, Optional, Any

class MessagePart(BaseModel):
    type: str
    text: str

class ClientMessage(BaseModel):
    # id: str
    role: str
    content: str
    parts: List[MessagePart]

class Request(BaseModel):
    id: str
    messages: List[ClientMessage]
    # trigger: str
