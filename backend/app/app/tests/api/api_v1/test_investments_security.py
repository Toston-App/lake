from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from app.core.config import settings


pytestmark = pytest.mark.asyncio
BASE_URL = f"{settings.API_V1_STR}/investments"


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/assets", {"symbol": "SECURE", "name": "Secure", "asset_type": "stock"}),
        ("PUT", "/assets/1", {"name": "Tampered"}),
        ("DELETE", "/assets/1", None),
    ],
)
async def test_normal_user_cannot_mutate_global_assets(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    response = await client.request(
        method,
        f"{BASE_URL}{path}",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert response.status_code in (400, 403)


@pytest.mark.parametrize(
    "path",
    [
        "/assets?limit=101",
        "/assets?skip=-1",
        "/holdings?limit=101",
        "/transactions?limit=101",
        f"/assets/search?q={'x' * 101}",
        f"/assets/search-external?q={'x' * 101}",
    ],
)
async def test_investment_queries_are_bounded(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    path: str,
) -> None:
    response = await client.get(f"{BASE_URL}{path}", headers=normal_user_token_headers)
    assert response.status_code == 422


async def test_holding_rejects_client_owned_valuation_fields(
    client: AsyncClient, normal_user_token_headers: dict[str, str]
) -> None:
    response = await client.put(
        f"{BASE_URL}/holdings/1",
        headers=normal_user_token_headers,
        json={"current_value_usd": 999999999},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("fees", -1),
        ("quantity", -1),
        ("exchange_rate_to_usd", 0),
        ("total_amount", 1000000),
    ],
)
async def test_transaction_rejects_unsafe_financial_inputs(
    client: AsyncClient,
    normal_user_token_headers: dict[str, str],
    field: str,
    value: float,
) -> None:
    payload = {
        "holding_id": 1,
        "account_id": 1,
        "transaction_type": "buy",
        "quantity": 1,
        "price_per_unit": 10,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        field: value,
    }
    response = await client.post(
        f"{BASE_URL}/transactions",
        headers=normal_user_token_headers,
        json=payload,
    )
    assert response.status_code == 422
