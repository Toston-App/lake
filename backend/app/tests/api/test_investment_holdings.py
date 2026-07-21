# ruff: noqa: ARG001
from decimal import Decimal
from unittest.mock import AsyncMock

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
    create_test_user,
)

PREFIX = "/api/v1/investments/holdings"


async def test_list_filters_and_unowned_account(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    usd = await create_test_asset(db_session, symbol="USDH")
    mxn = await create_test_asset(
        db_session, symbol="MXNH", currency=Currency.MXN, market=Market.BMV
    )
    await create_test_holding(
        db_session, owner_id=test_user.id, account_id=account.id, asset_id=usd.id
    )
    await create_test_holding(
        db_session,
        owner_id=test_user.id,
        account_id=account.id,
        asset_id=mxn.id,
        asset_currency=Currency.MXN,
    )
    assert len((await client.get(f"{PREFIX}?account_id={account.id}")).json()) == 2
    assert len((await client.get(f"{PREFIX}?currency=MXN")).json()) == 1
    other = await create_test_user(db_session, email="holding-other@example.com")
    other_account = await create_test_account(db_session, owner_id=other.id)
    assert (
        await client.get(f"{PREFIX}?account_id={other_account.id}")
    ).status_code == 404


async def test_create_from_asset_math_duplicate_and_validation(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    asset = await create_test_asset(db_session, symbol="CREATEH")
    payload = {
        "account_id": account.id,
        "asset_id": asset.id,
        "quantity": 2,
        "avg_cost_basis": 50,
        "cost_currency": "USD",
    }
    response = await client.post(PREFIX, json=payload)
    assert response.status_code == 200
    assert Decimal(response.json()["total_invested"]) == Decimal("100")
    assert Decimal(response.json()["current_value_mxn"]) == Decimal("1800")
    assert (await client.post(PREFIX, json=payload)).status_code == 409
    assert (
        await client.post(PREFIX, json={**payload, "quantity": 0})
    ).status_code == 422


async def test_create_rejects_inactive_and_unowned_account(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    inactive = await create_test_asset(db_session, symbol="INACTIVEH", is_active=False)
    payload = {
        "account_id": account.id,
        "asset_id": inactive.id,
        "quantity": 1,
        "avg_cost_basis": 1,
    }
    assert (await client.post(PREFIX, json=payload)).status_code == 409
    other = await create_test_user(db_session, email="unowned-h@example.com")
    other_account = await create_test_account(db_session, owner_id=other.id)
    assert (
        await client.post(PREFIX, json={**payload, "account_id": other_account.id})
    ).status_code == 404


async def test_create_from_provider_reuses_or_creates_asset(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user,
    monkeypatch,
    enable_investments,
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    resolved = ResolvedAsset(
        "PROV", "Provider", AssetType.STOCK, Market.NASDAQ, Currency.USD, "US"
    )
    monkeypatch.setattr(
        AssetResolverService, "resolve_from_yahoo", AsyncMock(return_value=resolved)
    )
    payload = {
        "account_id": account.id,
        "provider": "yahoo",
        "external_id": "PROV",
        "quantity": 1,
        "avg_cost_basis": 10,
    }
    created = await client.post(PREFIX, json=payload)
    assert created.status_code == 200
    holding = await db_session.get(Holding, created.json()["id"])
    first_asset_id = holding.asset_id

    second_account = await create_test_account(
        db_session, owner_id=test_user.id, name="Second"
    )
    reused = await client.post(
        PREFIX, json={**payload, "account_id": second_account.id}
    )
    assert reused.status_code == 200
    reused_holding = await db_session.get(Holding, reused.json()["id"])
    assert reused_holding.asset_id == first_asset_id


async def test_provider_identity_conflict(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user,
    monkeypatch,
    enable_investments,
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    await create_test_asset(db_session, symbol="CONFLICT", asset_type=AssetType.STOCK)
    resolved = ResolvedAsset(
        "CONFLICT", "Conflict", AssetType.ETF, Market.NASDAQ, Currency.USD, "US"
    )
    monkeypatch.setattr(
        AssetResolverService, "resolve_from_yahoo", AsyncMock(return_value=resolved)
    )
    response = await client.post(
        PREFIX,
        json={
            "account_id": account.id,
            "provider": "yahoo",
            "external_id": "CONFLICT",
            "quantity": 1,
            "avg_cost_basis": 10,
        },
    )
    assert response.status_code == 409


async def test_get_update_cross_user_and_delete(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    asset = await create_test_asset(db_session, symbol="MUTH")
    holding = await create_test_holding(
        db_session, owner_id=test_user.id, account_id=account.id, asset_id=asset.id
    )
    assert (await client.get(f"{PREFIX}/{holding.id}")).json()["symbol"] == "MUTH"
    updated = await client.put(
        f"{PREFIX}/{holding.id}", json={"quantity": 5, "avg_cost_basis": 20}
    )
    assert updated.status_code == 200
    assert Decimal(updated.json()["total_invested"]) == Decimal("100")
    assert (
        await client.put(f"{PREFIX}/{holding.id}", json={"current_value_usd": 999})
    ).status_code == 422

    other = await create_test_user(db_session, email="private-h@example.com")
    other_account = await create_test_account(db_session, owner_id=other.id)
    other_asset = await create_test_asset(db_session, symbol="PRIVATEH")
    private = await create_test_holding(
        db_session,
        owner_id=other.id,
        account_id=other_account.id,
        asset_id=other_asset.id,
    )
    assert (await client.get(f"{PREFIX}/{private.id}")).status_code == 404
    deleted = await client.delete(f"{PREFIX}/{holding.id}")
    assert deleted.status_code == 200
    await db_session.refresh(account)
    assert account.total_investments_usd == 0


async def test_delete_holding_with_transactions_is_immutable(
    client: AsyncClient, db_session: AsyncSession, test_user, enable_investments
):
    account = await create_test_account(db_session, owner_id=test_user.id)
    asset = await create_test_asset(db_session, symbol="LEDGERH")
    holding = await create_test_holding(
        db_session, owner_id=test_user.id, account_id=account.id, asset_id=asset.id
    )
    await create_test_investment_transaction(
        db_session, owner_id=test_user.id, account_id=account.id, holding_id=holding.id
    )
    assert (await client.delete(f"{PREFIX}/{holding.id}")).status_code == 409
