"""New holdings and assets, asset search and data export, through the shared tools."""

# ruff: noqa: ARG001
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.ai import investment_tools
from app.ai.confirmation import build_pending_preview, format_confirmation_message
from app.ai.dispatch import execute_tool
from app.ai.tools import RecordInvestmentTransactionArgs
from app.core.config import settings
from app.models import Asset, Holding, User
from app.models.asset import AssetType, Currency, Market
from app.models.data_export import DataExport, DataExportStatus
from app.services import asset_resolver
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.utils import create_test_account, create_test_asset, create_test_user


async def _run(db: AsyncSession, user: User, name: str, args: dict) -> dict:
    result, _ = await execute_tool(name, {"response_language": "en", **args}, db, user)
    return result


def _buy(**target) -> dict:
    return {"transaction_type": "buy", "quantity": 2, "price_per_unit": 10, **target}


# -- arguments --------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        {},
        {"account_id": 1},
        {"account_id": 1, "provider": "yahoo"},
        {"holding_id": 1, "account_id": 1},
        {"account_id": 1, "asset_id": 2, "provider": "yahoo", "external_id": "X"},
    ],
    ids=["nothing", "no-asset", "half-external", "holding-and-account", "both-assets"],
)
def test_record_needs_exactly_one_target(target) -> None:
    with pytest.raises(ValidationError):
        RecordInvestmentTransactionArgs(**_buy(**target), response_language="en")


# -- first purchase -----------------------------------------------------------------


async def test_first_purchase_of_a_known_asset_creates_the_holding(
    db_session: AsyncSession, test_user: User, enable_investments
) -> None:
    account = await create_test_account(
        db_session, owner_id=test_user.id, name="Broker"
    )
    asset = await create_test_asset(db_session, symbol="KNW")
    args = _buy(account_id=account.id, asset_id=asset.id)

    preview = await build_pending_preview(
        db_session, test_user, "record_investment_transaction", args
    )
    message = format_confirmation_message("record_investment_transaction", preview)
    assert preview["new_holding"] is True
    assert "adds a new holding to Broker" in message

    result = await _run(db_session, test_user, "record_investment_transaction", args)

    assert result["success"], result
    assert result["data"]["holding_created"] is True
    holding = await db_session.get(Holding, result["data"]["holding_id"])
    assert (holding.account_id, holding.asset_id, holding.quantity) == (
        account.id,
        asset.id,
        Decimal("2"),
    )


async def test_first_purchase_of_a_new_asset_resolves_it(
    db_session: AsyncSession, test_user: User, enable_investments, monkeypatch
) -> None:
    account = await create_test_account(db_session, owner_id=test_user.id)
    resolved = asset_resolver.ResolvedAsset(
        symbol="NEWCO",
        name="New Co",
        asset_type=AssetType.STOCK,
        market=Market.NASDAQ,
        currency=Currency.USD,
        country="US",
    )
    monkeypatch.setattr(
        asset_resolver.AssetResolverService,
        "resolve_from_yahoo",
        AsyncMock(return_value=resolved),
    )

    result = await _run(
        db_session,
        test_user,
        "record_investment_transaction",
        _buy(account_id=account.id, provider="yahoo", external_id="NEWCO"),
    )

    assert result["success"], result
    assert result["data"]["asset_created"] is True
    asset = (
        await db_session.execute(select(Asset).where(Asset.symbol == "NEWCO"))
    ).scalar_one()
    assert asset.id == result["data"]["asset_id"]


async def test_first_purchase_needs_the_users_account(
    db_session: AsyncSession, test_user: User, enable_investments
) -> None:
    other = await create_test_user(db_session, email="acct@example.com")
    foreign = await create_test_account(db_session, owner_id=other.id)
    asset = await create_test_asset(db_session, symbol="FRN")

    result = await _run(
        db_session,
        test_user,
        "record_investment_transaction",
        _buy(account_id=foreign.id, asset_id=asset.id),
    )

    assert result["success"] is False
    assert "Account not found" in result["error"]


# -- asset search -------------------------------------------------------------------


async def test_search_returns_known_and_provider_assets(
    db_session: AsyncSession, test_user: User, enable_investments, monkeypatch
) -> None:
    await create_test_asset(db_session, symbol="SRCH")
    stock = SimpleNamespace(symbol="SRCHX", provider="yahoo", external_id="SRCHX")
    monkeypatch.setattr(
        investment_tools.asset_search, "search_stocks", AsyncMock(return_value=[stock])
    )
    crypto = AsyncMock(return_value=[])
    monkeypatch.setattr(investment_tools.asset_search, "search_crypto", crypto)
    monkeypatch.setattr(investment_tools, "enforce_investment_rate_limit", AsyncMock())

    result = await _run(
        db_session,
        test_user,
        "search_investment_assets",
        {"query": "srch", "kind": "stocks"},
    )

    assert result["success"], result
    assert [a["symbol"] for a in result["data"]["existing"]] == ["SRCH"]
    assert result["data"]["stocks"][0]["external_id"] == "SRCHX"
    assert "unavailable" not in result["data"]
    crypto.assert_not_called()


async def test_one_provider_failing_keeps_the_others_results(
    db_session: AsyncSession, test_user: User, enable_investments, monkeypatch
) -> None:
    stock = SimpleNamespace(symbol="OKX", provider="yahoo", external_id="OKX")
    monkeypatch.setattr(
        investment_tools.asset_search, "search_stocks", AsyncMock(return_value=[stock])
    )
    monkeypatch.setattr(
        investment_tools.asset_search,
        "search_crypto",
        AsyncMock(side_effect=RuntimeError("coingecko down")),
    )
    limiter = AsyncMock()
    monkeypatch.setattr(investment_tools, "enforce_investment_rate_limit", limiter)

    result = await _run(
        db_session, test_user, "search_investment_assets", {"query": "ok"}
    )

    assert result["success"], result
    assert result["data"]["stocks"][0]["symbol"] == "OKX"
    assert result["data"]["unavailable"] == ["crypto"]
    # The assistant's throttle keys aren't the app search box's.
    keys = {call.args[0] for call in limiter.await_args_list}
    assert keys == {
        f"user:{test_user.id}:assistant-stocks",
        f"user:{test_user.id}:assistant-crypto",
    }


# -- export -------------------------------------------------------------------------


@pytest.fixture
def r2(monkeypatch) -> SimpleNamespace:
    presign = AsyncMock(return_value="https://signed.example/file.xlsx")
    monkeypatch.setattr("app.services.r2.is_configured", lambda: True)
    monkeypatch.setattr("app.services.r2.presign_get", presign)
    monkeypatch.setattr("app.services.r2.delete_object", AsyncMock())
    return SimpleNamespace(presign=presign)


async def test_export_points_to_the_app_page_never_the_file(
    db_session: AsyncSession, test_user: User, r2, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "WEB_APP_URL", "https://app.example.com/")
    args = {"response_language": "en"}
    first, _ = await execute_tool("export_data", args, db_session, test_user)
    again, _ = await execute_tool("export_data", args, db_session, test_user)

    assert first["success"] and first["data"]["already_running"] is False
    assert again["data"]["already_running"] is True

    row = await db_session.get(DataExport, first["data"]["export"]["id"])
    row.status = DataExportStatus.ready
    row.object_key = "exports/x.xlsx"
    row.completed_at = datetime.now(timezone.utc)
    await db_session.commit()

    ready, _ = await execute_tool("get_export_status", {}, db_session, test_user)

    assert ready["data"]["export"]["download_available"] is True
    assert ready["data"]["download_page"] == {
        "name": "Settings > Export data",
        "url": "https://app.example.com/settings/export",
    }
    # The workbook itself is never handed to the AI.
    assert "download" not in ready["data"]
    r2.presign.assert_not_called()


def test_export_page_without_a_configured_app_url(monkeypatch) -> None:
    from app.ai.export_tools import export_page

    monkeypatch.setattr(settings, "WEB_APP_URL", None)
    assert export_page() == {"name": "Settings > Export data"}


def test_export_tools_are_hidden_without_storage(monkeypatch) -> None:
    from app.ai.tool_specs import enabled_specs

    monkeypatch.setattr("app.services.r2.is_configured", lambda: False)
    names = {spec.name for spec in enabled_specs()}
    assert not {"export_data", "get_export_status"} & names


async def test_export_status_without_an_export(
    db_session: AsyncSession, test_user: User, r2
) -> None:
    result, _ = await execute_tool("get_export_status", {}, db_session, test_user)
    assert result["success"] is False
    assert "No export" in result["error"]


async def test_ledger_reports_the_asset_even_when_the_request_then_fails(
    db_session: AsyncSession, test_user: User, enable_investments
) -> None:
    """The endpoint's telemetry learns the asset before later rules refuse."""
    from app.schemas.investment_transaction import TransactionWithAssetCreate
    from app.services import investment_ledger
    from app.services.errors import ServiceError

    account = await create_test_account(db_session, owner_id=test_user.id)
    asset = await create_test_asset(db_session, symbol="TEL")
    seen: list[str] = []

    with pytest.raises(ServiceError) as exc_info:
        await investment_ledger.record_transaction_with_asset(
            db_session,
            test_user,
            TransactionWithAssetCreate(
                account_id=account.id,
                asset_id=asset.id,
                # A new holding can't start with a sale.
                transaction_type="sell",
                quantity=Decimal("1"),
                price_per_unit=Decimal("1"),
                executed_at=datetime.now(timezone.utc),
            ),
            idempotency_key="telemetry-test",
            on_asset=lambda found: seen.append(found.symbol),
        )

    assert exc_info.value.reason == "invalid_initial_transaction"
    assert seen == ["TEL"]


async def test_export_approval_names_the_file_it_replaces(
    db_session: AsyncSession, test_user: User, r2
) -> None:
    from app.ai.confirmation import requires_confirmation

    assert requires_confirmation("export_data", {})
    first, _ = await execute_tool(
        "export_data", {"response_language": "en"}, db_session, test_user
    )
    row = await db_session.get(DataExport, first["data"]["export"]["id"])

    running = await build_pending_preview(db_session, test_user, "export_data", {})
    assert running["already_running"] is True

    row.status = DataExportStatus.ready
    row.object_key = "exports/x.xlsx"
    row.completed_at = datetime.now(timezone.utc)
    await db_session.commit()

    preview = await build_pending_preview(db_session, test_user, "export_data", {})
    english = format_confirmation_message("export_data", preview, "en")
    spanish = format_confirmation_message("export_data", preview, "es")

    assert preview["replaces"] == row.filename
    assert preview["arguments"] == {"replaces": row.filename}
    assert english.startswith("Approve exporting all your data")
    assert f"replaces your previous export ({row.filename})" in english
    assert spanish.startswith("Aprueba exportar")
