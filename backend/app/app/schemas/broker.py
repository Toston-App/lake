"""
Pydantic schemas for broker API responses.
"""
from typing import Optional

from pydantic import BaseModel


class BrokerResponse(BaseModel):
    """Single broker for API response."""
    code: str
    name: str
    country: str
    broker_type: str
    website: Optional[str] = None
    logo_url: Optional[str] = None


class BrokersListResponse(BaseModel):
    """List of all brokers."""
    brokers: list[BrokerResponse]
    total: int
    
    # Optional grouped views
    by_country: Optional[dict[str, list[BrokerResponse]]] = None
    by_type: Optional[dict[str, list[BrokerResponse]]] = None
