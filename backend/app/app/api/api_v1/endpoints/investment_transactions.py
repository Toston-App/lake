"""
Investment transaction endpoints for the Investment Dashboard.
"""

import hashlib
import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.models.asset import ASSET_TYPE_TO_CLASS, Currency
from app.models.investment_transaction import TransactionType
from app.schemas.asset import AssetCreate
from app.schemas.holding import HoldingCreate
from app.schemas.investment_transaction import (
    InvestmentTransaction,
    InvestmentTransactionCreate,
    InvestmentTransactionDeletionResponse,
    InvestmentTransactionWithAsset,
    TransactionWithAssetCreate,
    TransactionWithAssetResponse,
)
from app.services.asset_resolver import AssetResolverService
from app.services.currency_converter import CurrencyConverter, CurrencyRateUnavailable
from app.services.investment_rate_limiter import enforce_investment_rate_limit
from app.utilities.investment_telemetry import (
    add_investment_context,
    complete_investment_event,
    fail_investment_event,
    investment_stage,
)
from app.utilities.redis import invalidate_user_cache

router = APIRouter()


def _request_fingerprint(payload: object) -> str:
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


async def _trusted_usd_mxn_rate() -> Decimal:
    try:
        return await CurrencyConverter.get_usd_to_mxn_rate()
    except CurrencyRateUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="A current USD/MXN rate is unavailable"
        ) from exc


def _convert_amount(
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


def _cash_balance_currency(current_user: models.User) -> Currency:
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
        raise HTTPException(
            status_code=422,
            detail="Cash balance currency must be USD or MXN",
        )
    return currency


async def _update_cash_balance_from_transaction(
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
        raise HTTPException(status_code=404, detail="Account or user not found")

    amount = transaction.total_amount
    if transaction.transaction_type == TransactionType.BUY:
        amount += transaction.fees
    converted_amount = _convert_amount(
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
        raise HTTPException(status_code=422, detail="Cash balance would be invalid")

    account.current_balance = float(account_balance)
    user.balance_total = float(user_balance)
    db.add(account)
    db.add(user)
    await db.flush()


async def _recover_idempotent_transaction(
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
        raise HTTPException(status_code=409, detail="Concurrent ledger write conflict")
    if existing.request_fingerprint != fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        )
    return existing


@router.get("", response_model=list[InvestmentTransactionWithAsset])
async def list_transactions(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    holding_id: int | None = Query(None, ge=1, description="Filter by holding"),
    account_id: int | None = Query(None, ge=1, description="Filter by account"),
    transaction_type: TransactionType | None = Query(
        None, description="Filter by type"
    ),
) -> Any:
    """
    List all investment transactions for the current user.

    Optional filters:
    - holding_id: Show transactions for a specific holding
    - account_id: Show transactions for a specific account
    - transaction_type: BUY, SELL, DIVIDEND, SPLIT, TRANSFER_IN, TRANSFER_OUT
    """
    add_investment_context(
        request,
        holding_id=holding_id,
        account_id=account_id,
        transaction_type=transaction_type,
    )
    with investment_stage(request, "database_query"):
        if holding_id is not None:
            transactions = await crud.investment_transaction.get_by_holding(
                db,
                holding_id=holding_id,
                owner_id=current_user.id,
                skip=skip,
                limit=limit,
            )
        elif account_id is not None:
            transactions = await crud.investment_transaction.get_by_account(
                db,
                account_id=account_id,
                owner_id=current_user.id,
                skip=skip,
                limit=limit,
            )
        elif transaction_type:
            transactions = await crud.investment_transaction.get_by_type(
                db,
                owner_id=current_user.id,
                transaction_type=transaction_type,
                skip=skip,
                limit=limit,
            )
        else:
            transactions = await crud.investment_transaction.get_by_owner(
                db,
                owner_id=current_user.id,
                skip=skip,
                limit=limit,
            )

    # Relationships are eagerly loaded in the CRUD query to avoid per-row queries.
    result = []
    for tx in transactions:
        holding = tx.holding
        asset = holding.asset if holding else None

        tx_data = InvestmentTransactionWithAsset(
            id=tx.id,
            owner_id=tx.owner_id,
            account_id=tx.account_id,
            holding_id=tx.holding_id,
            transaction_type=tx.transaction_type,
            quantity=tx.quantity,
            price_per_unit=tx.price_per_unit,
            currency=tx.currency,
            total_amount=tx.total_amount,
            fees=tx.fees,
            affects_cash_balance=tx.affects_cash_balance,
            exchange_rate_to_usd=tx.exchange_rate_to_usd,
            exchange_rate_to_mxn=tx.exchange_rate_to_mxn,
            notes=tx.notes,
            executed_at=tx.executed_at,
            symbol=asset.symbol if asset else None,
            asset_name=asset.name if asset else None,
        )
        result.append(tx_data)

    complete_investment_event(request, result_count=len(result))
    return result


@router.post("", response_model=InvestmentTransaction)
async def create_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_in: InvestmentTransactionCreate,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Record a new investment transaction (buy, sell, dividend, etc.)

    This will automatically update the holding's:
    - quantity (for BUY/SELL)
    - average cost basis (for BUY)
    - total invested amount

    And update the account's total_investments.
    """
    fingerprint = _request_fingerprint(transaction_in)
    existing_transaction = await crud.investment_transaction.get_by_idempotency_key(
        db, owner_id=current_user.id, idempotency_key=idempotency_key
    )
    if existing_transaction:
        if existing_transaction.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used with a different request",
            )
        return existing_transaction

    # Verify account belongs to user.
    add_investment_context(
        request,
        account_id=transaction_in.account_id,
        holding_id=transaction_in.holding_id,
        transaction_type=transaction_in.transaction_type,
    )
    with investment_stage(request, "ownership_check"):
        account = await crud.account.get_by_id(
            db, id=transaction_in.account_id, owner_id=current_user.id
        )
    if not account:
        fail_investment_event(request, reason="account_not_found")
        raise HTTPException(status_code=404, detail="Account not found")

    balance_currency = (
        _cash_balance_currency(current_user)
        if transaction_in.affects_cash_balance
        else None
    )

    candidate_holding = await crud.holding.get_by_id_and_owner(
        db,
        holding_id=transaction_in.holding_id,
        owner_id=current_user.id,
    )
    if not candidate_holding:
        fail_investment_event(request, reason="holding_not_found")
        raise HTTPException(status_code=404, detail="Holding not found")
    if candidate_holding.account_id != account.id:
        fail_investment_event(request, reason="account_holding_mismatch")
        raise HTTPException(status_code=403, detail="Not enough permissions")

    # Do not hold a database row lock while waiting for an upstream FX service.
    with investment_stage(request, "fx_lookup"):
        usd_mxn_rate = await _trusted_usd_mxn_rate()

    # Lock the owner-scoped holding so concurrent sells cannot overspend it.
    with investment_stage(request, "row_lock"):
        holding = await crud.holding.get_for_update_by_owner(
            db, holding_id=transaction_in.holding_id, owner_id=current_user.id
        )
    if not holding:
        fail_investment_event(request, reason="holding_not_found")
        raise HTTPException(status_code=404, detail="Holding not found")

    if holding.owner_id != current_user.id or holding.account_id != account.id:
        fail_investment_event(request, reason="holding_access_denied")
        raise HTTPException(status_code=403, detail="Not enough permissions")

    try:
        with investment_stage(request, "transaction_validation"):
            await _validate_transaction_against_holding(holding, transaction_in)
    except HTTPException:
        fail_investment_event(request, reason="position_rule_violation")
        raise

    # Currency and exchange rates are server-owned financial fields.
    transaction_currency = holding.asset.currency
    transaction_in = transaction_in.copy(
        update={
            "currency": transaction_currency,
            "exchange_rate_to_usd": Decimal("1")
            if transaction_currency.value == "USD"
            else Decimal("1") / usd_mxn_rate,
            "exchange_rate_to_mxn": Decimal("1")
            if transaction_currency.value == "MXN"
            else usd_mxn_rate,
        }
    )

    try:
        with investment_stage(request, "ledger_write"):
            transaction = await crud.investment_transaction.create_with_owner(
                db,
                obj_in=transaction_in,
                owner_id=current_user.id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                commit=False,
            )
    except IntegrityError:
        return await _recover_idempotent_transaction(
            db,
            owner_id=current_user.id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )

    # Update holding based on transaction type
    with investment_stage(request, "holding_update"):
        await _update_holding_from_transaction(
            db, holding, transaction, usd_mxn_rate=usd_mxn_rate, commit=False
        )
    if balance_currency is not None:
        with investment_stage(request, "cash_balance_update"):
            await _update_cash_balance_from_transaction(
                db,
                owner_id=current_user.id,
                account_id=account.id,
                transaction=transaction,
                balance_currency=balance_currency,
                usd_mxn_rate=usd_mxn_rate,
            )
    with investment_stage(request, "commit"):
        await db.commit()
        await db.refresh(transaction)
    if transaction.affects_cash_balance:
        await invalidate_user_cache(current_user.id)
    add_investment_context(
        request,
        transaction_id=transaction.id,
        asset_id=holding.asset_id,
        symbol=holding.asset.symbol,
    )
    complete_investment_event(request)
    return transaction


@router.post("/with-asset", response_model=TransactionWithAssetResponse)
async def create_transaction_with_asset(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_in: TransactionWithAssetCreate,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=128
    ),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Record a transaction with asset creation.

    This endpoint will:
    1. Create the asset if it doesn't exist (or use existing)
    2. Create the holding for the user if it doesn't exist (or use existing)
    3. Record the transaction
    4. Update the holding's cost basis and quantity

    This is ideal for quickly adding new investments without first
    manually creating assets and holdings.
    """
    fingerprint = _request_fingerprint(transaction_in)
    existing_transaction = await crud.investment_transaction.get_by_idempotency_key(
        db, owner_id=current_user.id, idempotency_key=idempotency_key
    )
    if existing_transaction:
        if existing_transaction.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used with a different request",
            )
        holding = await crud.holding.get(db, id=existing_transaction.holding_id)
        return TransactionWithAssetResponse(
            transaction=existing_transaction,
            asset_created=False,
            holding_created=False,
            asset_id=holding.asset_id,
            holding_id=holding.id,
        )

    # Validate ownership before resolving or creating any shared asset state.
    add_investment_context(
        request,
        account_id=transaction_in.account_id,
        asset_id=transaction_in.asset_id,
        provider=transaction_in.provider,
        transaction_type=transaction_in.transaction_type,
    )
    with investment_stage(request, "ownership_check"):
        account = await crud.account.get_by_id(
            db, id=transaction_in.account_id, owner_id=current_user.id
        )
    if not account:
        fail_investment_event(request, reason="account_not_found")
        raise HTTPException(status_code=404, detail="Account not found")

    balance_currency = (
        _cash_balance_currency(current_user)
        if transaction_in.affects_cash_balance
        else None
    )

    # Step 1: Get or create the asset
    asset_created = False
    if transaction_in.asset_id is not None:
        with investment_stage(request, "asset_lookup"):
            asset = await crud.asset.get(db, id=transaction_in.asset_id)
        if not asset:
            fail_investment_event(request, reason="asset_not_found")
            raise HTTPException(status_code=404, detail="Asset not found")
    else:
        if transaction_in.provider is not None and transaction_in.external_id:
            with investment_stage(request, "rate_limit"):
                await enforce_investment_rate_limit(
                    f"user:{current_user.id}:resolve-asset", 1.0
                )
            with investment_stage(request, "asset_resolution"):
                if transaction_in.provider.value == "yahoo":
                    resolved_asset = await AssetResolverService.resolve_from_yahoo(
                        transaction_in.external_id
                    )
                else:
                    resolved_asset = await AssetResolverService.resolve_from_coingecko(
                        transaction_in.external_id
                    )
            add_investment_context(request, symbol=resolved_asset.symbol)
        else:
            fail_investment_event(
                request, reason="asset_identity_missing", stage="validation"
            )
            raise HTTPException(
                status_code=422,
                detail="Provide asset_id or provider+external_id",
            )

        if resolved_asset.coingecko_id:
            existing_asset = await crud.asset.get_by_coingecko_id(
                db, coingecko_id=resolved_asset.coingecko_id
            )
            if existing_asset is None and await crud.asset.get_by_symbol(
                db, symbol=resolved_asset.symbol
            ):
                fail_investment_event(
                    request, reason="external_symbol_conflict", stage="identity_check"
                )
                raise HTTPException(
                    status_code=409,
                    detail="External asset symbol conflicts with an existing global asset",
                )
        else:
            existing_asset = await crud.asset.get_by_symbol(
                db, symbol=resolved_asset.symbol
            )

        if existing_asset:
            if (
                existing_asset.asset_type != resolved_asset.asset_type
                or existing_asset.market != resolved_asset.market
                or existing_asset.currency != resolved_asset.currency
            ):
                fail_investment_event(
                    request, reason="external_asset_conflict", stage="identity_check"
                )
                raise HTTPException(
                    status_code=409,
                    detail="External asset conflicts with an existing global asset",
                )
            asset = existing_asset
        else:
            asset_in = AssetCreate(
                symbol=resolved_asset.symbol,
                name=resolved_asset.name,
                asset_type=resolved_asset.asset_type,
                asset_class=ASSET_TYPE_TO_CLASS.get(resolved_asset.asset_type),
                currency=resolved_asset.currency,
                market=resolved_asset.market,
                country=resolved_asset.country,
                coingecko_id=resolved_asset.coingecko_id,
            )
            try:
                asset = await crud.asset.create(db, obj_in=asset_in, commit=False)
            except IntegrityError:
                await db.rollback()
                fail_investment_event(
                    request, reason="concurrent_asset_creation", stage="asset_write"
                )
                raise HTTPException(
                    status_code=409,
                    detail="Asset was concurrently created; retry the request",
                )
            asset_created = True

    # Resolve FX before locking an existing holding.
    add_investment_context(request, asset_id=asset.id, symbol=asset.symbol)
    with investment_stage(request, "fx_lookup"):
        usd_mxn_rate = await _trusted_usd_mxn_rate()

    # Step 3: Get or create the holding for this account
    holding_created = False
    existing_holding = await crud.holding.get_by_account_and_asset(
        db,
        account_id=account.id,
        asset_id=asset.id,
        owner_id=current_user.id,
    )

    if existing_holding:
        with investment_stage(request, "row_lock"):
            holding = await crud.holding.get_for_update_by_owner(
                db, holding_id=existing_holding.id, owner_id=current_user.id
            )
        if not holding:
            fail_investment_event(request, reason="holding_not_found")
            raise HTTPException(status_code=404, detail="Holding not found")
    else:
        if not asset.is_active:
            fail_investment_event(request, reason="asset_inactive", stage="validation")
            raise HTTPException(status_code=409, detail="Asset is inactive")
        if transaction_in.transaction_type not in (
            TransactionType.BUY,
            TransactionType.TRANSFER_IN,
        ):
            fail_investment_event(
                request, reason="invalid_initial_transaction", stage="validation"
            )
            raise HTTPException(
                status_code=400,
                detail="A new holding must start with a buy or transfer_in transaction",
            )
        # Create new holding with zero initial values
        holding_in = HoldingCreate(
            asset_id=asset.id,
            account_id=account.id,
            quantity=0.0,
            avg_cost_basis=0.0,
            cost_currency=asset.currency,
        )
        try:
            holding = await crud.holding.create_with_owner(
                db,
                obj_in=holding_in,
                owner_id=current_user.id,
                asset_currency=asset.currency,
                usd_mxn_rate=usd_mxn_rate,
                commit=False,
            )
        except IntegrityError:
            await db.rollback()
            fail_investment_event(
                request, reason="concurrent_holding_creation", stage="holding_write"
            )
            raise HTTPException(
                status_code=409,
                detail="A holding for this asset already exists in the account; retry the request",
            )
        holding_created = True

    # Step 3: Get exchange rates
    transaction_currency = asset.currency

    exchange_rate_to_usd = (
        Decimal("1")
        if transaction_currency.value == "USD"
        else Decimal("1") / usd_mxn_rate
    )
    exchange_rate_to_mxn = (
        Decimal("1") if transaction_currency.value == "MXN" else usd_mxn_rate
    )

    # Step 4: Create the transaction
    tx_in = InvestmentTransactionCreate(
        holding_id=holding.id,
        account_id=account.id,
        transaction_type=transaction_in.transaction_type,
        quantity=transaction_in.quantity,
        price_per_unit=transaction_in.price_per_unit,
        currency=transaction_currency,
        fees=transaction_in.fees,
        affects_cash_balance=transaction_in.affects_cash_balance,
        exchange_rate_to_usd=exchange_rate_to_usd,
        exchange_rate_to_mxn=exchange_rate_to_mxn,
        executed_at=transaction_in.executed_at,
        notes=transaction_in.notes,
    )

    try:
        with investment_stage(request, "transaction_validation"):
            await _validate_transaction_against_holding(holding, tx_in)
    except HTTPException:
        fail_investment_event(request, reason="position_rule_violation")
        raise

    try:
        with investment_stage(request, "ledger_write"):
            transaction = await crud.investment_transaction.create_with_owner(
                db,
                obj_in=tx_in,
                owner_id=current_user.id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                commit=False,
            )
    except IntegrityError:
        existing = await _recover_idempotent_transaction(
            db,
            owner_id=current_user.id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        existing_holding = existing.holding
        return TransactionWithAssetResponse(
            transaction=existing,
            asset_created=False,
            holding_created=False,
            asset_id=existing_holding.asset_id,
            holding_id=existing_holding.id,
        )

    # Step 5: Update holding based on transaction type
    with investment_stage(request, "holding_update"):
        await _update_holding_from_transaction(
            db, holding, transaction, usd_mxn_rate=usd_mxn_rate, commit=False
        )
    if balance_currency is not None:
        with investment_stage(request, "cash_balance_update"):
            await _update_cash_balance_from_transaction(
                db,
                owner_id=current_user.id,
                account_id=account.id,
                transaction=transaction,
                balance_currency=balance_currency,
                usd_mxn_rate=usd_mxn_rate,
            )
    with investment_stage(request, "commit"):
        await db.commit()
        await db.refresh(transaction)
    if transaction.affects_cash_balance:
        await invalidate_user_cache(current_user.id)
    add_investment_context(
        request,
        holding_id=holding.id,
        transaction_id=transaction.id,
    )
    complete_investment_event(
        request,
        asset_created=asset_created,
        holding_created=holding_created,
    )

    return TransactionWithAssetResponse(
        transaction=InvestmentTransaction(
            id=transaction.id,
            owner_id=transaction.owner_id,
            account_id=transaction.account_id,
            holding_id=transaction.holding_id,
            transaction_type=transaction.transaction_type,
            quantity=transaction.quantity,
            price_per_unit=transaction.price_per_unit,
            currency=transaction.currency,
            total_amount=transaction.total_amount,
            fees=transaction.fees,
            affects_cash_balance=transaction.affects_cash_balance,
            exchange_rate_to_usd=transaction.exchange_rate_to_usd,
            exchange_rate_to_mxn=transaction.exchange_rate_to_mxn,
            notes=transaction.notes,
            executed_at=transaction.executed_at,
        ),
        asset_created=asset_created,
        holding_created=holding_created,
        asset_id=asset.id,
        holding_id=holding.id,
    )


async def _update_holding_from_transaction(
    db: AsyncSession,
    holding: models.Holding,
    transaction: models.InvestmentTransaction,
    usd_mxn_rate: Decimal,
    commit: bool = True,
) -> None:
    """Update holding metrics after a transaction."""
    if transaction.transaction_type == TransactionType.BUY:
        # Acquisition fees increase cost basis, expressed in cost_currency.
        acquisition_cost = _convert_amount(
            transaction.total_amount + transaction.fees,
            from_currency=transaction.currency,
            to_currency=holding.cost_currency,
            usd_mxn_rate=usd_mxn_rate,
        )
        new_total_invested = holding.total_invested + acquisition_cost
        new_quantity = holding.quantity + transaction.quantity
        _validate_holding_state(new_quantity, new_total_invested)

        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
            usd_mxn_rate=usd_mxn_rate,
            commit=commit,
        )

    elif transaction.transaction_type == TransactionType.SELL:
        # Decrease quantity, proportionally reduce invested amount
        if transaction.quantity > holding.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot sell {transaction.quantity} shares. Only {holding.quantity} available.",
            )

        # Calculate proportional reduction in invested amount
        proportion_sold = transaction.quantity / holding.quantity
        amount_to_remove = holding.total_invested * proportion_sold

        new_quantity = holding.quantity - transaction.quantity
        new_total_invested = holding.total_invested - amount_to_remove
        _validate_holding_state(new_quantity, new_total_invested)

        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
            usd_mxn_rate=usd_mxn_rate,
            commit=commit,
        )

    elif transaction.transaction_type == TransactionType.DIVIDEND:
        # Dividends don't affect quantity or cost basis
        # They could be tracked separately for income reporting
        pass

    elif transaction.transaction_type == TransactionType.SPLIT:
        # Stock split: multiply quantity, divide cost basis
        # transaction.quantity represents the split ratio (e.g., 4 for 4:1 split)
        new_quantity = holding.quantity * transaction.quantity
        _validate_holding_state(new_quantity, holding.total_invested)
        # Total invested stays the same, but average cost decreases
        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=holding.total_invested,
            usd_mxn_rate=usd_mxn_rate,
            commit=commit,
        )

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
            raise HTTPException(
                status_code=400,
                detail="Transfer would result in negative quantity.",
            )

        # Adjust total invested proportionally
        if transaction.transaction_type == TransactionType.TRANSFER_IN:
            transfer_cost = _convert_amount(
                transaction.total_amount + transaction.fees,
                from_currency=transaction.currency,
                to_currency=holding.cost_currency,
                usd_mxn_rate=usd_mxn_rate,
            )
            new_total_invested = holding.total_invested + transfer_cost
        else:
            proportion = transaction.quantity / holding.quantity
            new_total_invested = holding.total_invested * (1 - proportion)

        _validate_holding_state(new_quantity, new_total_invested)

        await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=new_quantity,
            new_total_invested=new_total_invested,
            usd_mxn_rate=usd_mxn_rate,
            commit=commit,
        )


def _validate_holding_state(quantity: Decimal, total_invested: Decimal) -> None:
    if (
        not quantity.is_finite()
        or not total_invested.is_finite()
        or quantity < 0
        or quantity > 1e15
        or total_invested < 0
        or total_invested > 1e30
    ):
        raise HTTPException(
            status_code=422,
            detail="Transaction would create an unsafe holding value",
        )


async def _validate_transaction_against_holding(
    holding: models.Holding,
    transaction: InvestmentTransactionCreate,
) -> None:
    """Validate position-changing rules before any ledger row is written."""
    if transaction.transaction_type in (
        TransactionType.SELL,
        TransactionType.TRANSFER_OUT,
    ):
        if transaction.quantity > holding.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot remove {transaction.quantity} units. Only {holding.quantity} available.",
            )

    if (
        transaction.transaction_type == TransactionType.SPLIT
        and transaction.quantity <= 0
    ):
        raise HTTPException(status_code=400, detail="Split ratio must be positive")


@router.get("/{transaction_id}", response_model=InvestmentTransactionWithAsset)
async def get_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a specific transaction by ID.
    """
    add_investment_context(request, transaction_id=transaction_id)
    with investment_stage(request, "database_query"):
        if crud.user.is_superuser(current_user):
            transaction = await crud.investment_transaction.get(db, id=transaction_id)
        else:
            transaction = await crud.investment_transaction.get_by_id_and_owner(
                db, transaction_id=transaction_id, owner_id=current_user.id
            )
    if not transaction:
        fail_investment_event(request, reason="transaction_not_found")
        raise HTTPException(status_code=404, detail="Transaction not found")

    holding = await crud.holding.get(db, id=transaction.holding_id)
    asset = await crud.asset.get(db, id=holding.asset_id) if holding else None

    add_investment_context(
        request,
        account_id=transaction.account_id,
        holding_id=transaction.holding_id,
        asset_id=holding.asset_id if holding else None,
        symbol=asset.symbol if asset else None,
        transaction_type=transaction.transaction_type,
    )
    complete_investment_event(request)
    return InvestmentTransactionWithAsset(
        id=transaction.id,
        owner_id=transaction.owner_id,
        account_id=transaction.account_id,
        holding_id=transaction.holding_id,
        transaction_type=transaction.transaction_type,
        quantity=transaction.quantity,
        price_per_unit=transaction.price_per_unit,
        currency=transaction.currency,
        total_amount=transaction.total_amount,
        fees=transaction.fees,
        affects_cash_balance=transaction.affects_cash_balance,
        exchange_rate_to_usd=transaction.exchange_rate_to_usd,
        exchange_rate_to_mxn=transaction.exchange_rate_to_mxn,
        notes=transaction.notes,
        executed_at=transaction.executed_at,
        symbol=asset.symbol if asset else None,
        asset_name=asset.name if asset else None,
    )


@router.delete(
    "/{transaction_id}", response_model=InvestmentTransactionDeletionResponse
)
async def delete_transaction(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    transaction_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Investment transactions are immutable. Record a correcting transaction instead.
    """
    add_investment_context(request, transaction_id=transaction_id)
    with investment_stage(request, "database_query"):
        if crud.user.is_superuser(current_user):
            transaction = await crud.investment_transaction.get(db, id=transaction_id)
        else:
            transaction = await crud.investment_transaction.get_by_id_and_owner(
                db, transaction_id=transaction_id, owner_id=current_user.id
            )
    if not transaction:
        fail_investment_event(request, reason="transaction_not_found")
        raise HTTPException(status_code=404, detail="Transaction not found")

    fail_investment_event(
        request, reason="transaction_immutable", stage="immutability_check"
    )
    raise HTTPException(
        status_code=409,
        detail=(
            "Investment transactions are immutable because deleting one would corrupt "
            "the holding cost basis. Record a correcting transaction instead."
        ),
    )
