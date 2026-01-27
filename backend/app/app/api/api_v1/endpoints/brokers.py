"""
Broker API endpoints.

Provides access to the list of supported brokers with metadata.
"""
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Query

from app.models.broker import Broker, BROKER_INFO, BrokerType
from app.schemas.broker import BrokerResponse, BrokersListResponse

router = APIRouter()


def broker_to_response(broker: Broker) -> BrokerResponse:
    """Convert Broker enum + metadata to API response."""
    info = BROKER_INFO[broker]
    return BrokerResponse(
        code=info.code,
        name=info.name,
        country=info.country.value,
        broker_type=info.broker_type.value,
        website=info.website,
        logo_url=info.logo_url,
    )


@router.get("/", response_model=BrokersListResponse)
async def get_all_brokers(
    group_by: str = Query(None, description="Group by 'country' or 'type'")
) -> Any:
    """
    Get list of all available brokers with metadata.
    
    Optionally group by country or broker type.
    """
    brokers = [broker_to_response(b) for b in Broker]
    
    result = BrokersListResponse(brokers=brokers, total=len(brokers))
    
    if group_by == "country":
        by_country: dict[str, list[BrokerResponse]] = defaultdict(list)
        for b in brokers:
            by_country[b.country].append(b)
        result.by_country = dict(by_country)
    elif group_by == "type":
        by_type: dict[str, list[BrokerResponse]] = defaultdict(list)
        for b in brokers:
            by_type[b.broker_type].append(b)
        result.by_type = dict(by_type)
    
    return result


@router.get("/search", response_model=BrokersListResponse)
async def search_brokers(
    q: str = Query(..., min_length=1, description="Search query")
) -> Any:
    """
    Search brokers by name or code.
    
    Returns brokers where the query matches the code or name (case-insensitive).
    """
    query = q.lower()
    matching = []
    
    for broker in Broker:
        info = BROKER_INFO[broker]
        if query in info.code.lower() or query in info.name.lower():
            matching.append(broker_to_response(broker))
    
    return BrokersListResponse(brokers=matching, total=len(matching))


@router.get("/grouped", response_model=dict)
async def get_brokers_grouped() -> Any:
    """
    Get all brokers grouped by category for dropdown display.
    
    Returns brokers organized into:
    - US (non-crypto US brokers)
    - Mexico (non-crypto Mexican brokers)
    - Crypto (all crypto exchanges)
    - International (non-crypto international brokers)
    """
    groups: dict[str, list[BrokerResponse]] = {
        "US": [],
        "Mexico": [],
        "Crypto": [],
        "International": [],
    }
    
    for broker in Broker:
        info = BROKER_INFO[broker]
        response = broker_to_response(broker)
        
        # Crypto exchanges go to Crypto group regardless of country
        if info.broker_type == BrokerType.CRYPTO:
            groups["Crypto"].append(response)
        elif info.country.value == "US":
            groups["US"].append(response)
        elif info.country.value == "MX":
            groups["Mexico"].append(response)
        else:
            groups["International"].append(response)
    
    return {
        "groups": groups,
        "total": len(list(Broker)),
    }
