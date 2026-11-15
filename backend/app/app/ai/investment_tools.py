"""Investment tools: portfolio summary, holdings, investment transactions, and
recording a transaction on an existing holding.

They follow the REST endpoints' access rules (``services.investments``): the
feature flag, a paid plan, and read-only access for a lapsed subscription.
Recording goes through ``services.investment_ledger``, like
``POST /investments/transactions``, and is staged for approval. Adding a new
asset or holding is not exposed: it needs the app's asset search.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.ai.results import Handler, Outcome, ToolContext, ok, page_window, paged
from app.models.asset import AssetClass, Currency
from app.models.investment_transaction import TransactionType
from app.schemas.asset import ExternalAssetProvider
from app.schemas.investment_transaction import (
    InvestmentTransactionCreate,
    TransactionWithAssetCreate,
)
from app.services import asset_search
from app.services import investment_ledger as ledger
from app.services import investments as investment_service
from app.services.errors import NotFoundError
from app.services.investment_rate_limiter import enforce_investment_rate_limit

ENTITY_TYPE = "investment_transaction"
logger = logging.getLogger(__name__)


async def _summary(
    _args, db: AsyncSession, user: models.User, _ctx: ToolContext
) -> Outcome:
    await investment_service.investment_access(db, user)
    summary = await investment_service.portfolio_with_net_worth(db, user)
    return ok(summary), "portfolio_fetched"


async def _holdings(
    args, db: AsyncSession, user: models.User, _ctx: ToolContext
) -> Outcome:
    await investment_service.investment_access(db, user)
    window = page_window(args.page)
    if args.account_id is not None:
        account = await crud.account.get_by_id(db, id=args.account_id, owner_id=user.id)
        if account is None:
            raise NotFoundError("Account not found")
        holdings = await crud.holding.get_by_account(
            db, account_id=args.account_id, owner_id=user.id, **window
        )
    else:
        holdings = await crud.holding.get_filtered(
            db,
            owner_id=user.id,
            asset_class=AssetClass(args.asset_class) if args.asset_class else None,
            currency=Currency(args.currency) if args.currency else None,
            **window,
        )
    rows = await investment_service.holdings_with_assets(db, holdings)
    return paged(rows, args.page), "holdings_listed"


async def _transactions(
    args, db: AsyncSession, user: models.User, _ctx: ToolContext
) -> Outcome:
    await investment_service.investment_access(db, user)
    crud_tx = crud.investment_transaction
    common = {"owner_id": user.id, **page_window(args.page)}
    if args.holding_id is not None:
        transactions = await crud_tx.get_by_holding(
            db, holding_id=args.holding_id, **common
        )
    elif args.account_id is not None:
        transactions = await crud_tx.get_by_account(
            db, account_id=args.account_id, **common
        )
    elif args.transaction_type:
        transactions = await crud_tx.get_by_type(
            db, transaction_type=TransactionType(args.transaction_type), **common
        )
    else:
        transactions = await crud_tx.get_by_owner(db, **common)
    rows = [investment_service.transaction_with_asset(tx) for tx in transactions]
    return paged(rows, args.page), "investments_listed"


async def _owned_holding(db: AsyncSession, user: models.User, holding_id: int):
    holding = await crud.holding.get_by_id_and_owner(
        db, holding_id=holding_id, owner_id=user.id
    )
    if holding is None:
        raise NotFoundError("Holding not found")
    return holding


def _executed_at(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _transaction_fields(args) -> dict[str, Any]:
    return {
        "transaction_type": TransactionType(args.transaction_type),
        "quantity": Decimal(str(args.quantity)),
        "price_per_unit": Decimal(str(args.price_per_unit)),
        "fees": Decimal(str(args.fees)),
        "affects_cash_balance": args.affects_cash_balance,
        "executed_at": _executed_at(args.executed_at),
        "notes": args.notes,
    }


async def _record(
    args, db: AsyncSession, user: models.User, _ctx: ToolContext
) -> Outcome:
    if await investment_service.investment_access(db, user):
        raise investment_service.InvestmentsReadOnly()
    # Confirmation claims an action once, so each call is a new ledger entry.
    idempotency_key = f"assistant-{uuid.uuid4().hex}"
    if args.holding_id is not None:
        holding = await _owned_holding(db, user, args.holding_id)
        transaction = await ledger.record_transaction(
            db,
            user,
            InvestmentTransactionCreate(
                holding_id=holding.id,
                account_id=holding.account_id,
                **_transaction_fields(args),
            ),
            idempotency_key=idempotency_key,
        )
        return ok(transaction), "investment_recorded"

    result = await ledger.record_transaction_with_asset(
        db,
        user,
        TransactionWithAssetCreate(
            account_id=args.account_id,
            asset_id=args.asset_id,
            provider=ExternalAssetProvider(args.provider) if args.provider else None,
            external_id=args.external_id,
            **_transaction_fields(args),
        ),
        idempotency_key=idempotency_key,
    )
    return ok(
        {
            "transaction": result.transaction,
            "asset_id": result.asset_id,
            "holding_id": result.holding_id,
            "asset_created": result.asset_created,
            "holding_created": result.holding_created,
        }
    ), "investment_recorded"


# Matches the asset search a user sees in the app: a few of each kind.
_SEARCH_LIMIT = 10


async def _provider_search(
    user: models.User,
    kind: str,
    search: Any,
    query: str,
    unavailable: list[str],
) -> list:
    """One provider's matches, or [] (and ``kind`` noted) when it can't answer."""
    try:
        # The assistant's own throttle key: a search in the app's search box
        # at the same moment doesn't make this one fail.
        await enforce_investment_rate_limit(f"user:{user.id}:assistant-{kind}", 0.5)
        return (await search(query))[:_SEARCH_LIMIT]
    except Exception:  # noqa: BLE001 - a provider or its throttle failing is partial
        logger.warning("Asset search at %s failed", kind, exc_info=True)
        unavailable.append(kind)
        return []


async def _search_assets(
    args, db: AsyncSession, user: models.User, _ctx: ToolContext
) -> Outcome:
    await investment_service.investment_access(db, user)
    query = args.query.strip()
    existing = await crud.asset.search_assets(db, query=query, limit=_SEARCH_LIMIT)
    result: dict[str, Any] = {
        "existing": [
            {
                "asset_id": asset.id,
                "symbol": asset.symbol,
                "name": asset.name,
                "asset_type": asset.asset_type,
                "currency": asset.currency,
                "market": asset.market,
            }
            for asset in existing
        ],
        "stocks": [],
        "crypto": [],
    }
    # Each provider is asked separately: one being down or throttled doesn't
    # discard the other's results, and the model is told which is missing.
    unavailable: list[str] = []
    if args.kind in ("all", "stocks"):
        result["stocks"] = await _provider_search(
            user, "stocks", asset_search.search_stocks, query, unavailable
        )
    if args.kind in ("all", "crypto"):
        result["crypto"] = await _provider_search(
            user, "crypto", asset_search.search_crypto, query, unavailable
        )
    if unavailable:
        result["unavailable"] = unavailable
        result["retry_hint"] = (
            "Some providers could not be searched just now; try again in a few seconds."
        )
    result["next_step"] = (
        "Record with record_investment_transaction: holding_id for an existing "
        "holding (list_holdings); otherwise account_id plus asset_id for an "
        "existing asset, or provider and external_id for a new one."
    )
    return ok(result), "assets_searched"


INVESTMENT_HANDLERS: dict[str, Handler] = {
    "get_portfolio_summary": _summary,
    "list_holdings": _holdings,
    "list_investment_transactions": _transactions,
    "record_investment_transaction": _record,
    "search_investment_assets": _search_assets,
}


# -- preview and message ----------------------------------------------------------


async def _preview_record(db, user, tool_name: str, tool_args: dict) -> dict:
    await investment_service.investment_access(db, user)
    quantity = Decimal(str(tool_args["quantity"]))
    price = Decimal(str(tool_args["price_per_unit"]))
    preview: dict[str, Any] = {
        "kind": tool_name,
        "operation": "create",
        "entity_type": ENTITY_TYPE,
        "fields": dict(tool_args),
        "total_amount": str(quantity * price),
    }
    if tool_args.get("holding_id") is not None:
        holding = await _owned_holding(db, user, tool_args["holding_id"])
        preview.update(
            display_name=holding.asset.symbol,
            currency=holding.asset.currency.value,
            existing=jsonable_encoder(
                {"holding_id": holding.id, "quantity": holding.quantity}
            ),
        )
        return preview

    account = await crud.account.get_by_id(
        db, id=tool_args["account_id"], owner_id=user.id
    )
    if account is None:
        raise NotFoundError("Account not found")
    preview["labels"] = {"account_id": account.name}
    if tool_args.get("asset_id") is not None:
        asset = await crud.asset.get(db, id=tool_args["asset_id"])
        if asset is None:
            raise NotFoundError("Asset not found")
        held = await crud.holding.get_by_account_and_asset(
            db, account_id=account.id, asset_id=asset.id, owner_id=user.id
        )
        preview.update(
            display_name=asset.symbol,
            currency=asset.currency.value,
            new_holding=held is None,
        )
    else:
        # Resolved at the provider when the user approves.
        preview.update(
            display_name=tool_args.get("external_id"),
            currency="",
            new_holding=True,
            new_asset_provider=tool_args.get("provider"),
        )
    return preview


INVESTMENT_PREVIEW_BUILDERS = {"record_investment_transaction": _preview_record}

_TYPE_NAMES = {
    "en": {
        "buy": "buy",
        "sell": "sale",
        "dividend": "dividend",
        "split": "split",
        "transfer_in": "transfer in",
        "transfer_out": "transfer out",
    },
    "es": {
        "buy": "compra",
        "sell": "venta",
        "dividend": "dividendo",
        "split": "split",
        "transfer_in": "transferencia de entrada",
        "transfer_out": "transferencia de salida",
    },
}


# Spanish article for each transaction kind ("esta compra", "este dividendo").
_ES_ARTICLES = {"dividend": "este", "split": "este"}


def format_investment_message(preview: dict[str, Any], language: str) -> str:
    fields = preview.get("fields") or {}
    transaction_type = fields.get("transaction_type")
    kind = _TYPE_NAMES[language].get(transaction_type, "")
    symbol = preview.get("display_name") or "?"
    quantity = fields.get("quantity")
    price = fields.get("price_per_unit")
    currency = preview.get("currency") or ""
    account = (preview.get("labels") or {}).get("account_id")
    cash = fields.get("affects_cash_balance")
    if language == "es":
        article = _ES_ARTICLES.get(transaction_type, "esta")
        text = (
            f"Revisa {article} {kind} de **{quantity} {symbol}** a {price} "
            f"{currency} y aprueba para registrarla.".replace("  ", " ")
        )
        if preview.get("new_holding"):
            text += f" Se crea una nueva posición en {account}."
        return text + (" También cambia tu saldo en efectivo." if cash else "")
    text = (
        f"Review this {kind} of **{quantity} {symbol}** at {price} {currency}, "
        "then approve to record it.".replace(" ,", ",")
    )
    if preview.get("new_holding"):
        text += f" It adds a new holding to {account}."
    return text + (" It also changes your cash balance." if cash else "")
