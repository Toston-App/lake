import json
from enum import Enum
from pydantic import BaseModel
from typing import List, Optional, Any

class MessagePart(BaseModel):
    type: str
    text: str

class ClientMessage(BaseModel):
    # uncomment this if testing v5
    # id: str
    role: str
    parts: List[MessagePart]
    # comment this if testing v5
    content: str

class Request(BaseModel):
    id: str
    messages: List[ClientMessage]
    # uncomment this if testing v5
    # trigger: str
