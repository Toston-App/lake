import json
from enum import Enum
from pydantic import BaseModel
from typing import List, Optional, Any

class MessagePart(BaseModel):
    type: str
    text: str

class ClientMessage(BaseModel):
    # uncomment to use with vercel sdk v5
    # id: str
    role: str
    # comment to use with vercel sdk v5
    content: str
    parts: List[MessagePart]

class Request(BaseModel):
    id: str
    messages: List[ClientMessage]
    # uncomment to use with vercel sdk v5
    # trigger: str
