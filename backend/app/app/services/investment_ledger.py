"""The investment ledger: record a transaction against a holding.

Shared by ``POST /investments/transactions`` (and its ``/with-asset``
variant) and the chat/MCP ``record_investment_transaction`` tool. Errors are
``LedgerError`` (a ``ServiceError``), which the API reports with the same
status codes and messages as before. ``stage`` lets the endpoint time each
step for its telemetry; other callers leave it out.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.models.asset import ASSET_TYPE_TO_CLASS, Currency
from app.models.investment_transaction import TransactionType
from app.schemas.asset import AssetCreate
from app.schemas.holding import HoldingCreate
from app.schemas.investment_transaction import (
    InvestmentTransactionCreate,
    TransactionWithAssetCreate,
)
from app.services.asset_resolver import AssetResolverService
from app.services.errors import ServiceError
from app.services.investment_rate_limiter import enforce_investment_rate_limit
from app.services.investments import usd_mxn_rate
from app.utilities.redis import invalidate_user_cache

Stage = Callable[[str], contextlib.AbstractContextManager[Any]]
# Told which asset a request is about once it is known (for telemetry).
OnAsset = Callable[[models.Asset], None]


class LedgerError(ServiceError):
    """A ledger rule refused the transaction."""

    def __init__(
        self, status_code: int, message: str, reason: str, stage: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        # The telemetry stage to blame, when it isn't the current one.
        self.stage = stage


def _no_stage(_name: str) -> contextlib.AbstractContextManager[Any]:
    return contextlib.nullcontext()


def _no_asset(_asset: models.Asset) -> None:
    return None


def request_fingerprint(payload: object) -> str:
    if hasattr(payload, "dict"):
        data = payload.dict(
            exclude={"currency", "exchange_rate_to_usd", "exchange_rate_to_mxn"}
        )
        if data.get("affects_cash_balance") is False:
            data.pop("affects_cash_balance")
    else:
        data = payload
    encoded = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def convert_amount(
    amount: Decimal,
    *,
    from_currency: models.Currency,
    to_currency: models.Currency,
    usd_mxn_rate: Decimal,
) -> Decimal:
    if from_currency == to_currency:
        return amount
    if from_currency == models.Currency.USD:
        return amount * usd_mxn_rate
    return amount / usd_mxn_rate


def cash_balance_currency(current_user: models.User) -> Currency:
    # TODO: replace country with separate locale and currency fields. Until then,
    # accept both the legacy currency values and the newer locale values.
    currency_by_country = {
        "USD": Currency.USD,
        "en-US": Currency.USD,
        "MXN": Currency.MXN,
        "es-MX": Currency.MXN,
    }
    currency = currency_by_country.get(current_user.country or "")
    if currency is None:
        raise LedgerError(
            422, "Cash balance currency must be USD or MXN", "cash_currency"
        )
    return currency


async def update_cash_balance_from_transaction(
    db: AsyncSession,
    *,
    owner_id: int,
    account_id: int,
    transaction: models.InvestmentTransaction,
    balance_currency: Currency,
    usd_mxn_rate: Decimal,
) -> None:
    account = await crud.account.get_for_update_by_id(
        db, owner_id=owner_id, id=account_id
    )
    user = await crud.user.get_for_update(db, user_id=owner_id)
    if account is None or user is None:
        raise LedgerError(404, "Account or user not found", "account_not_found")

    amount = transaction.total_amount
    if transaction.transaction_type == TransactionType.BUY:
        amount += transaction.fees
    converted_amount = convert_amount(
        amount,
        from_currency=transaction.currency,
        to_currency=balance_currency,
        usd_mxn_rate=usd_mxn_rate,
    )
    delta = (
        -converted_amount
        if transaction.transaction_type == TransactionType.BUY
        else converted_amount
    )

    account_balance = Decimal(str(account.current_balance or 0)) + delta
    user_balance = Decimal(str(user.balance_total or 0)) + delta
    if not account_balance.is_finite() or not user_balance.is_finite():
        raise LedgerError(422, "Cash balance would be invalid", "invalid_balance")

    account.current_balance = float(account_balance)
    user.balance_total = float(user_balance)
    db.add(account)
    db.add(user)
    await db.flush()


async def recover_idempotent_transaction(
    db: AsyncSession,
    *,
    owner_id: int,
    idempotency_key: str,
    fingerprint: str,
) -> models.InvestmentTransaction:
    """Resolve a concurrent unique-key race after rolling back the losing write."""
    await db.rollback()
    existing = await crud.investment_transaction.get_by_idempotency_key(
        db, owner_id=owner_id, idempotency_key=idempotency_key
    )
    if existing is None:
        raise LedgerError(409, "Concurrent ledger write conflict", "ledger_conflict")
    if existing.request_fingerprint != fingerprint:
        raise LedgerError(
            409,
            "Idempotency-Key was already used with a different request",
            "idempotency_mismatch",
        )
    return existing


def validate_holding_state(quantity: Decimal, total_invested: Decimal) -> None:
    if (
        not quantity.is_finite()
        or not total_invested.is_finite()
        or quantity < 0
        or quantity > 1e15
        or total_invested < 0
        or total_invested > 1e30
    ):
        raise LedgerError(
            422,
            "Transaction would create an unsafe holding value",
            "unsafe_holding_value",
        )


async def validate_transaction_against_holding(
    holding: models.Holding,
    transaction: InvestmentTransactionCreate,
) -> None:
    """Validate position-changing rules before any ledger row is written."""
    if transaction.transaction_type in (
        TransactionType.SELL,
        TransactionType.TRANSFER_OUT,
    ):
        if transaction.quantity > holding.quantity:
            raise LedgerError(
                400,
                f"Cannot remove {transaction.quantity} units. Only {holding.quantity} available.",
                "position_rule_violation",
            )

    if (
        transaction.transaction_type == TransactionType.SPLIT
        and transaction.quantity <= 0
    ):
        raise LedgerError(
            400, "Split ratio must be positive", "position_rule_violation"
        )


async def update_holding_from_transaction(
    db: AsyncSession,
    holding: models.Holding,
    transaction: models.InvestmentTransaction,
    usd_mxn_rate: Decimal,
    commit: bool = True,
) -> None:
    """Update holding metrics after a transaction."""
    if transaction.transaction_type == TransactionType.BUY:
        # Acquisition fees increase cost basis, expressed in cost_currency.
        acquisition_cost = convert_amount(
            transaction.total_amount + transaction.fees,
            from_currency=transaction.currency,
            to_currency=holding.cost_currency,
            usd_mxn_rate=usd_mxn_rate,
        )
        new_quantity = holding.quantity + transaction.quantity
        new_total_invested = holding.total_invested + acquisition_cost

    elif transaction.transaction_type == TransactionType.SELL:
        # Decrease quantity, proportionally reduce invested amount
        if transaction.quantity > holding.quantity:
            raise LedgerError(
                400,
                f"Cannot sell {transaction.quantity} shares. Only {holding.quantity} available.",
                "position_rule_violation",
            )
        proportion_sold = transaction.quantity / holding.quantity
        new_quantity = holding.quantity - transaction.quantity
        new_total_invested = holding.total_invested - (
            holding.total_invested * proportion_sold
        )

    elif transaction.transaction_type == TransactionType.DIVIDEND:
        # Dividends don't affect quantity or cost basis.
        return

    elif transaction.transaction_type == TransactionType.SPLIT:
        # Stock split: multiply quantity; total invested stays, average cost
        # drops. transaction.quantity is the ratio (4 for a 4:1 split).
        new_quantity = holding.quantity * transaction.quantity
        new_total_invested = holding.total_invested

    elif transaction.transaction_type in (
        TransactionType.TRANSFER_IN,
        TransactionType.TRANSFER_OUT,
    ):
        # Transfers: adjust quantity without changing cost basis per share
        multiplier = (
            1 if transaction.transaction_type == TransactionType.TRANSFER_IN else -1
        )
        new_quantity = holding.quantity + (transaction.quantity * multiplier)
        if new_quantity < 0:
            raise LedgerError(
                400,
                "Transfer would result in negative quantity.",
                "position_rule_violation",
            )
        if transaction.transaction_type == TransactionType.TRANSFER_IN:
            new_total_invested = holding.total_invested + convert_amount(
                transaction.total_amount + transaction.fees,
                from_currency=transaction.currency,
                to_currency=holding.cost_currency,
                usd_mxn_rate=usd_mxn_rate,
            )
        else:
            proportion = transaction.quantity / holding.quantity
            new_total_invested = holding.total_invested * (1 - proportion)
    else:
        return

    validate_holding_state(new_quantity, new_total_invested)
    await crud.holding.recalculate_cost_basis(
        db,
        holding=holding,
        new_quantity=new_quantity,
        new_total_invested=new_total_invested,
        usd_mxn_rate=usd_mxn_rate,
        commit=commit,
    )


async def record_transaction(
    db: AsyncSession,
    user: models.User,
    transaction_in: InvestmentTransactionCreate,
    *,
    idempotency_key: str,
    stage: Stage = _no_stage,
    on_asset: OnAsset = _no_asset,
) -> models.InvestmentTransaction:
    """Record a buy, sell, dividend, split or transfer on one of the user's holdings.

    Updates the holding's quantity, cost basis and total invested and, when
    ``affects_cash_balance``, the account and user cash balance. Replaying the
    same request with the same key returns the first transaction.
    """
    fingerprint = request_fingerprint(transaction_in)
    existing = await crud.investment_transaction.get_by_idempotency_key(
        db, owner_id=user.id, idempotency_key=idempotency_key
    )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise LedgerError(
                409,
                "Idempotency-Key was already used with a different request",
                "idempotency_mismatch",
            )
        return existing

    with stage("ownership_check"):
        account = await crud.account.get_by_id(
            db, id=transaction_in.account_id, owner_id=user.id
        )
    if not account:
        raise LedgerError(404, "Account not found", "account_not_found")

    balance_currency = (
        cash_balance_currency(user) if transaction_in.affects_cash_balance else None
    )

    candidate = await crud.holding.get_by_id_and_owner(
        db, holding_id=transaction_in.holding_id, owner_id=user.id
    )
    if not candidate:
        raise LedgerError(404, "Holding not found", "holding_not_found")
    if candidate.account_id != account.id:
        raise LedgerError(403, "Not enough permissions", "account_holding_mismatch")

    # Do not hold a database row lock while waiting for an upstream FX service.
    with stage("fx_lookup"):
        rate = await usd_mxn_rate()

    # Lock the owner-scoped holding so concurrent sells cannot overspend it.
    with stage("row_lock"):
        holding = await crud.holding.get_for_update_by_owner(
            db, holding_id=transaction_in.holding_id, owner_id=user.id
        )
    if not holding:
        raise LedgerError(404, "Holding not found", "holding_not_found")
    if holding.owner_id != user.id or holding.account_id != account.id:
        raise LedgerError(403, "Not enough permissions", "holding_access_denied")
    on_asset(holding.asset)

    with stage("transaction_validation"):
        await validate_transaction_against_holding(holding, transaction_in)

    # Currency and exchange rates are server-owned financial fields.
    transaction_in = transaction_in.copy(
        update=_server_fx_fields(holding.asset.currency, rate)
    )
    transaction, _recovered = await _write_and_apply(
        db,
        user,
        holding=holding,
        account_id=account.id,
        transaction_in=transaction_in,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        rate=rate,
        balance_currency=balance_currency,
        stage=stage,
    )
    return transaction


def _server_fx_fields(currency: Currency, rate: Decimal) -> dict[str, Any]:
    """The transaction's currency and exchange rates, which the server owns."""
    return {
        "currency": currency,
        "exchange_rate_to_usd": Decimal("1")
        if currency.value == "USD"
        else Decimal("1") / rate,
        "exchange_rate_to_mxn": Decimal("1") if currency.value == "MXN" else rate,
    }


async def _write_and_apply(
    db: AsyncSession,
    user: models.User,
    *,
    holding: models.Holding,
    account_id: int,
    transaction_in: InvestmentTransactionCreate,
    idempotency_key: str,
    fingerprint: str,
    rate: Decimal,
    balance_currency: Currency | None,
    stage: Stage,
) -> tuple[models.InvestmentTransaction, bool]:
    """Write the ledger row, apply it to the holding and cash, and commit.

    Returns the transaction and whether it was a concurrent duplicate that
    was recovered instead of written (then nothing else was applied).
    """
    try:
        with stage("ledger_write"):
            transaction = await crud.investment_transaction.create_with_owner(
                db,
                obj_in=transaction_in,
                owner_id=user.id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                commit=False,
            )
    except IntegrityError:
        existing = await recover_idempotent_transaction(
            db,
            owner_id=user.id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        return existing, True

    with stage("holding_update"):
        await update_holding_from_transaction(
            db, holding, transaction, usd_mxn_rate=rate, commit=False
        )
    if balance_currency is not None:
        with stage("cash_balance_update"):
            await update_cash_balance_from_transaction(
                db,
                owner_id=user.id,
                account_id=account_id,
                transaction=transaction,
                balance_currency=balance_currency,
                usd_mxn_rate=rate,
            )
    with stage("commit"):
        await db.commit()
        await db.refresh(transaction)
    if transaction.affects_cash_balance:
        await invalidate_user_cache(user.id)
    return transaction, False


@dataclass(frozen=True)
class WithAssetResult:
    transaction: models.InvestmentTransaction
    asset_created: bool
    holding_created: bool
    asset_id: int
    holding_id: int


async def _resolve_external_asset(
    db: AsyncSession,
    user: models.User,
    transaction_in: TransactionWithAssetCreate,
    stage: Stage,
) -> tuple[models.Asset, bool]:
    """The global asset for a provider id: an existing one, or a new unsaved one."""
    if transaction_in.provider is None or not transaction_in.external_id:
        raise LedgerError(
            422,
            "Provide asset_id or provider+external_id",
            "asset_identity_missing",
            stage="validation",
        )
    with stage("rate_limit"):
        await enforce_investment_rate_limit(f"user:{user.id}:resolve-asset", 1.0)
    with stage("asset_resolution"):
        if transaction_in.provider.value == "yahoo":
            resolved = await AssetResolverService.resolve_from_yahoo(
                transaction_in.external_id
            )
        else:
            resolved = await AssetResolverService.resolve_from_coingecko(
                transaction_in.external_id
            )

    if resolved.coingecko_id:
        existing = await crud.asset.get_by_coingecko_id(
            db, coingecko_id=resolved.coingecko_id
        )
        if existing is None and await crud.asset.get_by_symbol(
            db, symbol=resolved.symbol
        ):
            raise LedgerError(
                409,
                "External asset symbol conflicts with an existing global asset",
                "external_symbol_conflict",
                stage="identity_check",
            )
    else:
        existing = await crud.asset.get_by_symbol(db, symbol=resolved.symbol)

    if existing:
        if (
            existing.asset_type != resolved.asset_type
            or existing.market != resolved.market
            or existing.currency != resolved.currency
        ):
            raise LedgerError(
                409,
                "External asset conflicts with an existing global asset",
                "external_asset_conflict",
                stage="identity_check",
            )
        return existing, False

    asset_in = AssetCreate(
        symbol=resolved.symbol,
        name=resolved.name,
        asset_type=resolved.asset_type,
        asset_class=ASSET_TYPE_TO_CLASS.get(resolved.asset_type),
        currency=resolved.currency,
        market=resolved.market,
        country=resolved.country,
        coingecko_id=resolved.coingecko_id,
    )
    try:
        asset = await crud.asset.create(db, obj_in=asset_in, commit=False)
    except IntegrityError:
        await db.rollback()
        raise LedgerError(
            409,
            "Asset was concurrently created; retry the request",
            "concurrent_asset_creation",
            stage="asset_write",
        ) from None
    return asset, True


async def _holding_for(
    db: AsyncSession,
    user: models.User,
    *,
    account_id: int,
    asset: models.Asset,
    transaction_type: TransactionType,
    rate: Decimal,
    stage: Stage,
) -> tuple[models.Holding, bool]:
    """The account's holding of ``asset``, locked; or a new empty one."""
    existing = await crud.holding.get_by_account_and_asset(
        db, account_id=account_id, asset_id=asset.id, owner_id=user.id
    )
    if existing:
        with stage("row_lock"):
            holding = await crud.holding.get_for_update_by_owner(
                db, holding_id=existing.id, owner_id=user.id
            )
        if not holding:
            raise LedgerError(404, "Holding not found", "holding_not_found")
        return holding, False

    if not asset.is_active:
        raise LedgerError(409, "Asset is inactive", "asset_inactive", "validation")
    if transaction_type not in (TransactionType.BUY, TransactionType.TRANSFER_IN):
        raise LedgerError(
            400,
            "A new holding must start with a buy or transfer_in transaction",
            "invalid_initial_transaction",
            stage="validation",
        )
    holding_in = HoldingCreate(
        asset_id=asset.id,
        account_id=account_id,
        quantity=0.0,
        avg_cost_basis=0.0,
        cost_currency=asset.currency,
    )
    try:
        holding = await crud.holding.create_with_owner(
            db,
            obj_in=holding_in,
            owner_id=user.id,
            asset_currency=asset.currency,
            usd_mxn_rate=rate,
            commit=False,
        )
    except IntegrityError:
        await db.rollback()
        raise LedgerError(
            409,
            "A holding for this asset already exists in the account; retry the request",
            "concurrent_holding_creation",
            stage="holding_write",
        ) from None
    return holding, True


async def record_transaction_with_asset(
    db: AsyncSession,
    user: models.User,
    transaction_in: TransactionWithAssetCreate,
    *,
    idempotency_key: str,
    stage: Stage = _no_stage,
    on_asset: OnAsset = _no_asset,
) -> WithAssetResult:
    """Record a transaction, creating the asset and the holding when needed.

    The asset is ``asset_id`` or resolved from ``provider`` + ``external_id``
    (created globally if new). A new holding must start with a buy or a
    transfer in. Replaying the same request with the same key returns the
    first transaction.
    """
    fingerprint = request_fingerprint(transaction_in)
    existing = await crud.investment_transaction.get_by_idempotency_key(
        db, owner_id=user.id, idempotency_key=idempotency_key
    )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise LedgerError(
                409,
                "Idempotency-Key was already used with a different request",
                "idempotency_mismatch",
            )
        holding = await crud.holding.get(db, id=existing.holding_id)
        return WithAssetResult(existing, False, False, holding.asset_id, holding.id)

    # Validate ownership before resolving or creating any shared asset state.
    with stage("ownership_check"):
        account = await crud.account.get_by_id(
            db, id=transaction_in.account_id, owner_id=user.id
        )
    if not account:
        raise LedgerError(404, "Account not found", "account_not_found")
    balance_currency = (
        cash_balance_currency(user) if transaction_in.affects_cash_balance else None
    )

    if transaction_in.asset_id is not None:
        with stage("asset_lookup"):
            asset = await crud.asset.get(db, id=transaction_in.asset_id)
        if not asset:
            raise LedgerError(404, "Asset not found", "asset_not_found")
        asset_created = False
    else:
        asset, asset_created = await _resolve_external_asset(
            db, user, transaction_in, stage
        )
    on_asset(asset)

    # Resolve FX before locking an existing holding.
    with stage("fx_lookup"):
        rate = await usd_mxn_rate()
    holding, holding_created = await _holding_for(
        db,
        user,
        account_id=account.id,
        asset=asset,
        transaction_type=transaction_in.transaction_type,
        rate=rate,
        stage=stage,
    )

    tx_in = InvestmentTransactionCreate(
        holding_id=holding.id,
        account_id=account.id,
        transaction_type=transaction_in.transaction_type,
        quantity=transaction_in.quantity,
        price_per_unit=transaction_in.price_per_unit,
        fees=transaction_in.fees,
        affects_cash_balance=transaction_in.affects_cash_balance,
        executed_at=transaction_in.executed_at,
        notes=transaction_in.notes,
        **_server_fx_fields(asset.currency, rate),
    )
    with stage("transaction_validation"):
        await validate_transaction_against_holding(holding, tx_in)

    transaction, recovered = await _write_and_apply(
        db,
        user,
        holding=holding,
        account_id=account.id,
        transaction_in=tx_in,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        rate=rate,
        balance_currency=balance_currency,
        stage=stage,
    )
    if recovered:
        # A concurrent request with the same key won; report what it created.
        return WithAssetResult(
            transaction,
            False,
            False,
            transaction.holding.asset_id,
            transaction.holding.id,
        )
    return WithAssetResult(
        transaction, asset_created, holding_created, asset.id, holding.id
    )
