"""
Unified price fetcher service that coordinates all price sources.
"""

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.models.asset import Asset, AssetClass, Currency
from app.models.asset_price import AssetPrice
from app.schemas.asset_price import AssetPriceCreate
from app.services.coingecko import CoinGeckoService
from app.services.currency_converter import CurrencyConverter
from app.services.provider_errors import ProviderUnavailable
from app.services.yahoo_finance import YahooFinanceService

logger = logging.getLogger(__name__)


class PriceFetcher:
    """
    Unified service for fetching and storing asset prices.

    Coordinates between different price sources based on asset type:
    - Stocks/ETFs: Yahoo Finance
    - Crypto: CoinGecko
    - Bonds/CETES/Mutual Funds: Manual entry (no auto-fetch)
    """

    @classmethod
    async def fetch_and_store_price(
        cls,
        db: AsyncSession,
        asset: Asset,
    ) -> AssetPrice | None:
        """
        Fetch current price for an asset and store it in the database.

        Args:
            db: Database session
            asset: Asset to fetch price for

        Returns:
            Created AssetPrice or None if fetch fails
        """
        try:
            price_data = await cls.fetch_price(asset)
        except (ArithmeticError, ValueError) as exc:
            logger.error("Invalid upstream price for %s: %s", asset.symbol, exc)
            raise ProviderUnavailable("Price provider returned invalid data") from exc
        if not price_data:
            return None

        try:
            # Price history and all affected valuations are one atomic mutation.
            asset_price = await crud.asset_price.create_with_commit(
                db, obj_in=price_data, commit=False
            )
            await cls._update_holdings_for_asset(db, asset, price_data)
            await db.commit()
            await db.refresh(asset_price)
            return asset_price
        except Exception:
            await db.rollback()
            raise

    @classmethod
    async def fetch_price(cls, asset: Asset) -> AssetPriceCreate | None:
        """
        Fetch current price for an asset without storing.

        Returns AssetPriceCreate schema or None if fetch fails.
        """
        # Determine which service to use based on asset type
        if asset.asset_class == AssetClass.CRYPTO:
            return await cls._fetch_crypto_price(asset)
        elif asset.asset_class == AssetClass.EQUITIES:
            return await cls._fetch_stock_price(asset)
        elif asset.asset_class in (AssetClass.FIXED_INCOME, AssetClass.FUNDS):
            # Manual entry assets - can't auto-fetch
            logger.info(f"Asset {asset.symbol} requires manual price entry")
            return None
        else:
            logger.warning(
                f"Unknown asset class for {asset.symbol}: {asset.asset_class}"
            )
            return None

    @classmethod
    async def _fetch_stock_price(cls, asset: Asset) -> AssetPriceCreate | None:
        """Fetch price for stocks/ETFs from Yahoo Finance."""
        stock_price = await YahooFinanceService.get_price(asset.symbol, asset.market)

        if not stock_price:
            return None

        # Get exchange rate for currency conversion
        usd_mxn_rate = await CurrencyConverter.get_usd_to_mxn_rate()

        # Calculate prices in both currencies
        native_price = Decimal(str(stock_price.price))
        if stock_price.currency == Currency.USD:
            price_usd = native_price
            price_mxn = native_price * usd_mxn_rate
        else:  # MXN
            price_mxn = native_price
            price_usd = native_price / usd_mxn_rate

        return AssetPriceCreate(
            asset_id=asset.id,
            price=native_price,
            currency=stock_price.currency,
            price_usd=price_usd,
            price_mxn=price_mxn,
            open_price=stock_price.open_price,
            high_price=stock_price.high_price,
            low_price=stock_price.low_price,
            previous_close=stock_price.previous_close,
            volume=stock_price.volume,
            change=stock_price.change,
            change_percent=stock_price.change_percent,
        )

    @classmethod
    async def _fetch_crypto_price(cls, asset: Asset) -> AssetPriceCreate | None:
        """Fetch price for cryptocurrencies from CoinGecko."""
        crypto_price = await CoinGeckoService.get_price(
            asset.symbol,
            coingecko_id=asset.coingecko_id,
        )

        if not crypto_price:
            return None

        return AssetPriceCreate(
            asset_id=asset.id,
            price=crypto_price.price_usd,
            currency=Currency.USD,
            price_usd=crypto_price.price_usd,
            price_mxn=crypto_price.price_mxn,
            volume=crypto_price.volume_24h,
            change_percent=crypto_price.change_24h_percent,
        )

    @classmethod
    async def refresh_all_prices(
        cls,
        db: AsyncSession,
        owner_id: int | None = None,
    ) -> tuple[int, list[str]]:
        """
        Refresh prices for all active assets.

        Args:
            db: Database session
            owner_id: If provided, only refresh assets held by this user

        Returns:
            Tuple of (updated_count, failed_symbols)
        """
        # Get assets to refresh
        if owner_id:
            # Get unique assets from user's holdings
            holdings = await crud.holding.get_by_owner(
                db, owner_id=owner_id, limit=None
            )
            asset_ids = {h.asset_id for h in holdings}
            assets = [await crud.asset.get(db, id=aid) for aid in asset_ids]
            assets = [a for a in assets if a and a.is_active]
        else:
            # Get all active assets
            assets = await crud.asset.get_multi_filtered(db, is_active=True, limit=1000)

        updated_count = 0
        failed_symbols = []

        for asset in assets:
            try:
                # A database-backed freshness check also protects deployments with
                # multiple workers from repeatedly fetching the same shared asset.
                if not await crud.asset_price.is_stale(
                    db, asset_id=asset.id, max_age_minutes=1
                ):
                    continue
                result = await cls.fetch_and_store_price(db, asset)
                if result:
                    updated_count += 1
                else:
                    failed_symbols.append(asset.symbol)
            except Exception as e:
                logger.error(f"Error refreshing price for {asset.symbol}: {e}")
                failed_symbols.append(asset.symbol)

        return updated_count, failed_symbols

    @classmethod
    async def _update_holdings_for_asset(
        cls,
        db: AsyncSession,
        asset: Asset,
        price_data: AssetPriceCreate,
    ) -> None:
        """Update all holdings' current values after price refresh."""
        # Get all holdings for this asset
        from sqlalchemy.sql.expression import select

        from app.models.holding import Holding

        result = await db.execute(
            select(Holding).filter(Holding.asset_id == asset.id).with_for_update()
        )
        holdings = result.scalars().all()

        for holding in holdings:
            await crud.holding.update_holding_value(
                db,
                holding=holding,
                current_price=price_data.price,
                price_usd=price_data.price_usd,
                price_mxn=price_data.price_mxn,
                commit=False,
            )

        # Recalculate total_investments for all affected accounts
        account_ids = {h.account_id for h in holdings}
        for account_id in account_ids:
            await crud.account.recalculate_total_investments(
                db, account_id=account_id, commit=False
            )

    @classmethod
    async def get_current_price(
        cls,
        db: AsyncSession,
        asset: Asset,
        max_age_minutes: int = 15,
        allow_fetch: bool = True,
    ) -> AssetPriceCreate | AssetPrice | None:
        """
        Get current price for an asset, fetching if stale.

        Args:
            db: Database session
            asset: Asset to get price for
            max_age_minutes: Maximum age of cached price before refetching

        Returns:
            Current price data or None
        """
        latest = await crud.asset_price.get_latest_by_asset(db, asset_id=asset.id)
        is_stale = await crud.asset_price.is_stale(
            db, asset_id=asset.id, max_age_minutes=max_age_minutes
        )
        if latest and (not is_stale or not allow_fetch):
            return AssetPriceCreate(
                asset_id=latest.asset_id,
                price=latest.price,
                currency=latest.currency,
                price_usd=latest.price_usd,
                price_mxn=latest.price_mxn,
                open_price=latest.open_price,
                high_price=latest.high_price,
                low_price=latest.low_price,
                previous_close=latest.previous_close,
                volume=latest.volume,
                change=latest.change,
                change_percent=latest.change_percent,
            )

        # Fetch fresh price
        if allow_fetch:
            return await cls.fetch_and_store_price(db, asset)
        return None
