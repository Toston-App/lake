"""
Holdings management endpoints for the Investment Dashboard.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models
from app.api import deps
from app.models.asset import ASSET_TYPE_TO_CLASS, AssetClass, Currency
from app.schemas.asset import AssetCreate
from app.schemas.holding import (
    Holding,
    HoldingCreate,
    HoldingDeletionResponse,
    HoldingUpdate,
    HoldingWithAsset,
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

router = APIRouter()


async def _trusted_usd_mxn_rate() -> Decimal:
    try:
        return await CurrencyConverter.get_usd_to_mxn_rate()
    except CurrencyRateUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="A current USD/MXN rate is unavailable"
        ) from exc


@router.get("", response_model=list[HoldingWithAsset])
async def list_holdings(
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    account_id: int | None = Query(None, ge=1, description="Filter by account"),
    asset_class: AssetClass | None = Query(None, description="Filter by asset class"),
    currency: Currency | None = Query(None, description="Filter by asset currency"),
) -> Any:
    """
    List all holdings for the current user.

    Optional filters:
    - account_id: Filter by specific account
    - asset_class: Filter by EQUITIES, FIXED_INCOME, CRYPTO, or FUNDS
    - currency: Filter by USD or MXN exposure
    """
    if account_id is not None:
        add_investment_context(request, account_id=account_id)
        # Validate account ownership
        with investment_stage(request, "ownership_check"):
            account = await crud.account.get_by_id(
                db, id=account_id, owner_id=current_user.id
            )
        if not account:
            fail_investment_event(request, reason="account_not_found")
            raise HTTPException(status_code=404, detail="Account not found")
        with investment_stage(request, "database_query"):
            holdings = await crud.holding.get_by_account(
                db,
                account_id=account_id,
                owner_id=current_user.id,
                skip=skip,
                limit=limit,
            )
    else:
        with investment_stage(request, "database_query"):
            holdings = await crud.holding.get_filtered(
                db,
                owner_id=current_user.id,
                asset_class=asset_class,
                currency=currency,
                skip=skip,
                limit=limit,
            )

    latest_prices = await crud.asset_price.get_latest_prices(
        db, asset_ids=[holding.asset_id for holding in holdings]
    )

    # Enrich with asset details
    result = []
    for holding in holdings:
        asset = holding.asset

        latest_price = latest_prices.get(asset.id)

        holding_data = HoldingWithAsset(
            id=holding.id,
            owner_id=holding.owner_id,
            account_id=holding.account_id,
            asset_id=holding.asset_id,
            quantity=holding.quantity,
            avg_cost_basis=holding.avg_cost_basis,
            cost_currency=holding.cost_currency,
            total_invested=holding.total_invested,
            current_value=holding.current_value,
            current_value_mxn=holding.current_value_mxn,
            current_value_usd=holding.current_value_usd,
            unrealized_gain_loss=holding.unrealized_gain_loss,
            unrealized_gain_loss_pct=holding.unrealized_gain_loss_pct,
            # Asset details
            symbol=asset.symbol,
            asset_name=asset.name,
            asset_class=asset.asset_class,
            asset_type=asset.asset_type,
            asset_currency=asset.currency,
            market=asset.market,
            sector=asset.sector,
            country=asset.country,
        )

        if latest_price:
            holding_data.current_price = latest_price.price
            holding_data.price_change = latest_price.change
            holding_data.price_change_percent = latest_price.change_percent

        result.append(holding_data)

    complete_investment_event(request, result_count=len(result))
    return result


@router.post("", response_model=Holding)
async def create_holding(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    holding_in: HoldingCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create a new holding.

    If a holding already exists for this asset in the account, use PUT to update it.
    """
    # Verify account belongs to user
    add_investment_context(
        request,
        account_id=holding_in.account_id,
        asset_id=holding_in.asset_id,
        provider=holding_in.provider,
    )
    with investment_stage(request, "ownership_check"):
        account = await crud.account.get_by_id(
            db, id=holding_in.account_id, owner_id=current_user.id
        )
    if not account:
        fail_investment_event(request, reason="account_not_found")
        raise HTTPException(status_code=404, detail="Account not found")

    if holding_in.quantity <= 0:
        fail_investment_event(
            request, reason="initial_quantity_not_positive", stage="validation"
        )
        raise HTTPException(
            status_code=422, detail="Initial holding quantity must be positive"
        )

    # Resolve asset from existing ID or external identifier
    if holding_in.asset_id is not None:
        with investment_stage(request, "asset_lookup"):
            asset = await crud.asset.get(db, id=holding_in.asset_id)
        if not asset:
            fail_investment_event(request, reason="asset_not_found")
            raise HTTPException(status_code=404, detail="Asset not found")
    else:
        if not holding_in.provider or not holding_in.external_id:
            fail_investment_event(
                request, reason="asset_identity_missing", stage="validation"
            )
            raise HTTPException(
                status_code=422,
                detail="Provide asset_id or provider+external_id",
            )

        with investment_stage(request, "rate_limit"):
            await enforce_investment_rate_limit(
                f"user:{current_user.id}:resolve-asset", 1.0
            )
        with investment_stage(request, "asset_resolution"):
            if holding_in.provider.value == "yahoo":
                resolved_asset = await AssetResolverService.resolve_from_yahoo(
                    holding_in.external_id
                )
            else:
                resolved_asset = await AssetResolverService.resolve_from_coingecko(
                    holding_in.external_id
                )
        add_investment_context(request, symbol=resolved_asset.symbol)

        if resolved_asset.coingecko_id:
            existing_asset = await crud.asset.get_by_coingecko_id(
                db, coingecko_id=resolved_asset.coingecko_id
            )
            if existing_asset is None and await crud.asset.get_by_symbol(
                db, symbol=resolved_asset.symbol
            ):
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
                raise HTTPException(
                    status_code=409,
                    detail="Asset was concurrently created; retry the request",
                )

    if not asset.is_active:
        fail_investment_event(request, reason="asset_inactive", stage="validation")
        raise HTTPException(status_code=409, detail="Asset is inactive")

    holding_in.asset_id = asset.id
    add_investment_context(request, asset_id=asset.id, symbol=asset.symbol)
    usd_mxn_rate = await _trusted_usd_mxn_rate()

    # Check if holding already exists in this account
    existing = await crud.holding.get_by_account_and_asset(
        db,
        account_id=account.id,
        asset_id=asset.id,
        owner_id=current_user.id,
    )
    if existing:
        fail_investment_event(
            request, reason="holding_already_exists", stage="identity_check"
        )
        raise HTTPException(
            status_code=409,
            detail=f"You already have a holding for {asset.symbol} in this account.",
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
            request, reason="holding_identity_conflict", stage="database_write"
        )
        raise HTTPException(
            status_code=409,
            detail="A holding for this asset already exists in the account",
        )
    with investment_stage(request, "commit"):
        await db.commit()
        await db.refresh(holding)
    add_investment_context(request, holding_id=holding.id)
    complete_investment_event(request, holding_created=True)
    return holding


@router.get("/{holding_id}", response_model=HoldingWithAsset)
async def get_holding(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    holding_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get a specific holding by ID.
    """
    add_investment_context(request, holding_id=holding_id)
    with investment_stage(request, "database_query"):
        if crud.user.is_superuser(current_user):
            holding = await crud.holding.get(db, id=holding_id)
        else:
            holding = await crud.holding.get_by_id_and_owner(
                db, holding_id=holding_id, owner_id=current_user.id
            )
    if not holding:
        fail_investment_event(request, reason="holding_not_found")
        raise HTTPException(status_code=404, detail="Holding not found")

    asset = await crud.asset.get(db, id=holding.asset_id)
    latest_price = await crud.asset_price.get_latest_by_asset(db, asset_id=asset.id)

    result = HoldingWithAsset(
        id=holding.id,
        owner_id=holding.owner_id,
        account_id=holding.account_id,
        asset_id=holding.asset_id,
        quantity=holding.quantity,
        avg_cost_basis=holding.avg_cost_basis,
        cost_currency=holding.cost_currency,
        total_invested=holding.total_invested,
        current_value=holding.current_value,
        current_value_mxn=holding.current_value_mxn,
        current_value_usd=holding.current_value_usd,
        unrealized_gain_loss=holding.unrealized_gain_loss,
        unrealized_gain_loss_pct=holding.unrealized_gain_loss_pct,
        symbol=asset.symbol,
        asset_name=asset.name,
        asset_class=asset.asset_class,
        asset_type=asset.asset_type,
        asset_currency=asset.currency,
        market=asset.market,
        sector=asset.sector,
        country=asset.country,
    )

    if latest_price:
        result.current_price = latest_price.price
        result.price_change = latest_price.change
        result.price_change_percent = latest_price.change_percent

    add_investment_context(
        request,
        account_id=holding.account_id,
        asset_id=holding.asset_id,
        symbol=asset.symbol,
    )
    complete_investment_event(request, price_available=latest_price is not None)
    return result


@router.put("/{holding_id}", response_model=Holding)
async def update_holding(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    holding_id: int = Path(..., ge=1),
    holding_in: HoldingUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update a holding (quantity, cost basis, etc.)

    For recording buy/sell transactions, use the transactions endpoint instead.
    """
    add_investment_context(request, holding_id=holding_id)
    owner_id = None if crud.user.is_superuser(current_user) else current_user.id
    if owner_id is None:
        existing_holding = await crud.holding.get(db, id=holding_id)
        owner_id = existing_holding.owner_id if existing_holding else None
    holding = (
        await crud.holding.get_for_update_by_owner(
            db, holding_id=holding_id, owner_id=owner_id
        )
        if owner_id is not None
        else None
    )
    if not holding:
        fail_investment_event(request, reason="holding_not_found", stage="row_lock")
        raise HTTPException(status_code=404, detail="Holding not found")
    add_investment_context(
        request,
        account_id=holding.account_id,
        asset_id=holding.asset_id,
    )

    quantity = (
        holding_in.quantity if holding_in.quantity is not None else holding.quantity
    )
    avg_cost_basis = (
        holding_in.avg_cost_basis
        if holding_in.avg_cost_basis is not None
        else holding.avg_cost_basis
    )
    if holding_in.quantity is not None or holding_in.avg_cost_basis is not None:
        usd_mxn_rate = await _trusted_usd_mxn_rate()
        total_invested = quantity * avg_cost_basis
        if not total_invested.is_finite() or total_invested > Decimal("1e30"):
            fail_investment_event(
                request, reason="unsafe_holding_value", stage="validation"
            )
            raise HTTPException(status_code=422, detail="Invested total is too large")
        holding = await crud.holding.recalculate_cost_basis(
            db,
            holding=holding,
            new_quantity=quantity,
            new_total_invested=total_invested,
            usd_mxn_rate=usd_mxn_rate,
            commit=False,
        )

    if holding_in.cost_currency is not None:
        holding.cost_currency = holding_in.cost_currency
    holding.updated_at = datetime.now(timezone.utc)
    db.add(holding)
    with investment_stage(request, "commit"):
        await db.commit()
        await db.refresh(holding)
    complete_investment_event(request)

    return holding


@router.delete("/{holding_id}", response_model=HoldingDeletionResponse)
async def delete_holding(
    *,
    request: Request,
    db: AsyncSession = Depends(deps.async_get_db),
    holding_id: int = Path(..., ge=1),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete a holding and all associated transactions.
    """
    add_investment_context(request, holding_id=holding_id)
    owner_id = None if crud.user.is_superuser(current_user) else current_user.id
    if owner_id is None:
        existing_holding = await crud.holding.get(db, id=holding_id)
        owner_id = existing_holding.owner_id if existing_holding else None
    holding = (
        await crud.holding.get_for_update_by_owner(
            db, holding_id=holding_id, owner_id=owner_id
        )
        if owner_id is not None
        else None
    )
    if not holding:
        fail_investment_event(request, reason="holding_not_found", stage="row_lock")
        raise HTTPException(status_code=404, detail="Holding not found")

    asset = await crud.asset.get(db, id=holding.asset_id)
    account_id = holding.account_id

    transactions = await crud.investment_transaction.get_by_holding(
        db,
        holding_id=holding.id,
        owner_id=holding.owner_id,
        skip=0,
        limit=1,
    )
    if transactions:
        fail_investment_event(
            request, reason="holding_has_immutable_ledger", stage="immutability_check"
        )
        raise HTTPException(
            status_code=409,
            detail="A holding with investment transactions cannot be deleted",
        )

    add_investment_context(
        request,
        account_id=account_id,
        asset_id=holding.asset_id,
        symbol=asset.symbol,
    )
    with investment_stage(request, "database_write"):
        await crud.holding.remove_with_commit(db, id=holding_id, commit=False)

        # Recalculate account total_investments after deletion
        await crud.account.recalculate_total_investments(
            db, account_id=account_id, commit=False
        )
    with investment_stage(request, "commit"):
        await db.commit()

    complete_investment_event(request)

    return HoldingDeletionResponse(message=f"Holding for {asset.symbol} deleted")
