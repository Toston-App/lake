# ruff: noqa: ARG001
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.models.asset import AssetType, Currency, Market
from app.models.holding import Holding
from app.services.asset_resolver import AssetResolverService, ResolvedAsset
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.utils import (
    create_test_account,
    create_test_asset,
    create_test_holding,
    create_test_investment_transaction,
)

PREFIX = "/api/v1/investments/transactions"


async def _position(
    db: AsyncSession,
    user,
    *,
    symbol="TXAPI",
    quantity=Decimal("10"),
    cost=Decimal("100"),
):
    account = await create_test_account(db, owner_id=user.id)
    asset = await create_test_asset(db, symbol=symbol)
    holding = await create_test_holding(
        db,
        owner_id=user.id,
        account_id=account.id,
        asset_id=asset.id,
        quantity=quantity,
        avg_cost_basis=cost,
    )
    return account, asset, holding


def _payload(account, holding, kind, *, quantity="1", price="100", fees="0"):
    return {
        "account_id": account.id,
        "holding_id": holding.id,
        "transaction_type": kind,
        "quantity": quantity,
        "price_per_unit": price,
        "fees": fees,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def test_buy_weighted_cost_and_server_owned_currency(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account, _, holding = await _position(db_session, test_user)
    payload = _payload(account, holding, "buy", quantity="2", price="50", fees="10")
    payload.update(
        {
            "currency": "MXN",
            "exchange_rate_to_usd": "999",
            "exchange_rate_to_mxn": "999",
        }
    )
    response = await client.post(
        PREFIX, headers={"Idempotency-Key": "buy-math"}, json=payload
    )
    assert response.status_code == 200
    assert Decimal(response.json()["total_amount"]) == Decimal("100")
    assert response.json()["currency"] == "USD"
    assert Decimal(response.json()["exchange_rate_to_usd"]) == Decimal("1")
    assert Decimal(response.json()["exchange_rate_to_mxn"]) == Decimal("18")
    await db_session.refresh(holding)
    assert holding.quantity == Decimal("12")
    assert float(holding.total_invested) == pytest.approx(1110)
    assert float(holding.avg_cost_basis) == pytest.approx(92.5)


async def test_sell_math_oversell_and_fees_validation(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account, _, holding = await _position(db_session, test_user, symbol="SELLAPI")
    response = await client.post(
        PREFIX,
        headers={"Idempotency-Key": "sell-math"},
        json=_payload(account, holding, "sell", quantity="4", price="120", fees="5"),
    )
    assert response.status_code == 200
    assert Decimal(response.json()["total_amount"]) == Decimal("475")
    await db_session.refresh(holding)
    assert holding.quantity == Decimal("6")
    assert holding.total_invested == Decimal("600")
    assert holding.avg_cost_basis == Decimal("100")
    assert (
        await client.post(
            PREFIX,
            headers={"Idempotency-Key": "oversell"},
            json=_payload(account, holding, "sell", quantity="7"),
        )
    ).status_code == 400
    assert (
        await client.post(
            PREFIX,
            headers={"Idempotency-Key": "bad-fees"},
            json=_payload(account, holding, "sell", quantity="1", price="1", fees="2"),
        )
    ).status_code == 422


@pytest.mark.parametrize(
    "kind,expected_quantity,expected_total",
    [
        ("dividend", Decimal("10"), Decimal("1000")),
        ("split", Decimal("20"), Decimal("1000")),
        ("transfer_in", Decimal("12"), Decimal("1100")),
        ("transfer_out", Decimal("8"), Decimal("800")),
    ],
)
async def test_other_transaction_position_math(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user,
    enable_investments,
    kind,
    expected_quantity,
    expected_total,
):
    account, _, holding = await _position(
        db_session, test_user, symbol=f"K{kind[:5].upper()}"
    )
    quantity = "2" if kind != "dividend" else "1"
    price = "50" if kind == "transfer_in" else "0"
    response = await client.post(
        PREFIX,
        headers={"Idempotency-Key": f"kind-{kind}"},
        json=_payload(account, holding, kind, quantity=quantity, price=price),
    )
    assert response.status_code == 200
    await db_session.refresh(holding)
    assert holding.quantity == expected_quantity
    assert holding.total_invested == expected_total


async def test_account_holding_mismatch_is_forbidden(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account, _, holding = await _position(db_session, test_user, symbol="MISMATCH")
    other = await create_test_account(db_session, owner_id=test_user.id, name="Wrong")
    response = await client.post(
        PREFIX,
        headers={"Idempotency-Key": "mismatch"},
        json={**_payload(account, holding, "buy"), "account_id": other.id},
    )
    assert response.status_code == 403


async def test_idempotency_replay_conflict_and_required_header(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account, _, holding = await _position(db_session, test_user, symbol="IDEMP")
    payload = _payload(account, holding, "dividend", price="5")
    first = await client.post(PREFIX, headers={"Idempotency-Key": "same"}, json=payload)
    replay = await client.post(
        PREFIX, headers={"Idempotency-Key": "same"}, json=payload
    )
    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    conflict = await client.post(
        PREFIX,
        headers={"Idempotency-Key": "same"},
        json={**payload, "notes": "changed"},
    )
    assert conflict.status_code == 409
    assert (await client.post(PREFIX, json=payload)).status_code == 422


async def test_with_asset_initial_rule(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user,
    monkeypatch,
    enable_investments,
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    resolved = ResolvedAsset(
        "ATOM", "Atomic", AssetType.STOCK, Market.NASDAQ, Currency.USD, "US"
    )
    monkeypatch.setattr(
        AssetResolverService, "resolve_from_yahoo", AsyncMock(return_value=resolved)
    )
    base = {
        "account_id": account.id,
        "provider": "yahoo",
        "external_id": "ATOM",
        "quantity": 2,
        "price_per_unit": 25,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
    invalid = await client.post(
        f"{PREFIX}/with-asset",
        headers={"Idempotency-Key": "initial-sell"},
        json={**base, "transaction_type": "sell"},
    )
    assert invalid.status_code == 400


async def test_with_asset_atomic_creation(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user,
    monkeypatch,
    enable_investments,
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    resolved = ResolvedAsset(
        "ATOM", "Atomic", AssetType.STOCK, Market.NASDAQ, Currency.USD, "US"
    )
    monkeypatch.setattr(
        AssetResolverService, "resolve_from_yahoo", AsyncMock(return_value=resolved)
    )
    base = {
        "account_id": account.id,
        "provider": "yahoo",
        "external_id": "ATOM",
        "quantity": 2,
        "price_per_unit": 25,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
    created = await client.post(
        f"{PREFIX}/with-asset",
        headers={"Idempotency-Key": "initial-buy"},
        json={**base, "transaction_type": "buy"},
    )
    assert created.status_code == 200
    data = created.json()
    assert data["asset_created"] is True
    assert data["holding_created"] is True
    holding = await db_session.get(Holding, data["holding_id"])
    assert holding.quantity == Decimal("2")
    assert holding.total_invested == Decimal("50")


async def test_get_list_filter_priority_and_immutable_delete(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account, asset, holding = await _position(db_session, test_user, symbol="READTX")
    buy = await create_test_investment_transaction(
        db_session,
        owner_id=test_user.id,
        account_id=account.id,
        holding_id=holding.id,
    )
    response = await client.get(f"{PREFIX}/{buy.id}")
    assert response.status_code == 200
    assert response.json()["symbol"] == asset.symbol
    listed = await client.get(f"{PREFIX}?holding_id={holding.id}&transaction_type=sell")
    assert [item["id"] for item in listed.json()] == [buy.id]
    assert (await client.delete(f"{PREFIX}/{buy.id}")).status_code == 409
    assert (await client.delete(f"{PREFIX}/999999")).status_code == 404
