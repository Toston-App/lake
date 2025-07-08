import json
from enum import Enum
from pydantic import BaseModel
from typing import List, Optional, Any

class ClientMessage(BaseModel):
    role: str
    content: str

class Request(BaseModel):
    id: str
    messages: List[ClientMessage]
