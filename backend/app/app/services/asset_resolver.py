from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

from app.models.asset import AssetType, Currency, Market
from app.services.coingecko import CoinGeckoService
from app.services.yahoo_finance import YahooFinanceService


@dataclass
class ResolvedAsset:
    symbol: str
    name: str
    asset_type: AssetType
    market: Market
    currency: Currency
    country: str
    coingecko_id: Optional[str] = None


class AssetResolverService:
    @classmethod
    async def resolve_from_yahoo(cls, external_id: str) -> ResolvedAsset:
        ticker = external_id.upper().strip()
        if not ticker:
            raise HTTPException(status_code=422, detail="external_id is required")

        results = await YahooFinanceService.search_symbol(ticker)

        exact_match = None
        for item in results:
            symbol = (item.get("symbol") or "").upper().strip()
            if symbol == ticker:
                exact_match = item
                break

        if exact_match is None:
            raise HTTPException(status_code=404, detail=f"External asset '{ticker}' not found")

        symbol = (exact_match.get("symbol") or "").upper().strip()
        name = (exact_match.get("name") or symbol).strip()
        quote_type = (exact_match.get("type") or "").upper()
        exchange = (exact_match.get("exchange") or "").upper()

        is_mexican = symbol.endswith(".MX") or exchange == "MEX"
        clean_symbol = symbol.replace(".MX", "")

        if is_mexican:
            market = Market.BMV
            currency = Currency.MXN
            country = "MX"
        elif exchange in {"NYQ", "NYSE"}:
            market = Market.NYSE
            currency = Currency.USD
            country = "US"
        else:
            market = Market.NASDAQ
            currency = Currency.USD
            country = "US"

        asset_type = AssetType.ETF if quote_type == "ETF" else AssetType.STOCK

        return ResolvedAsset(
            symbol=clean_symbol,
            name=name,
            asset_type=asset_type,
            market=market,
            currency=currency,
            country=country,
        )

    @classmethod
    async def resolve_from_coingecko(cls, external_id: str) -> ResolvedAsset:
        cg_id = external_id.strip().lower()
        if not cg_id:
            raise HTTPException(status_code=422, detail="external_id is required")

        results = await CoinGeckoService.search_coins(cg_id)

        exact_match = None
        for coin in results:
            if (coin.get("coingecko_id") or "").lower() == cg_id:
                exact_match = coin
                break

        if exact_match is None:
            raise HTTPException(status_code=404, detail=f"External asset '{cg_id}' not found")

        symbol = (exact_match.get("symbol") or "").upper().strip()
        name = (exact_match.get("name") or symbol).strip()

        if not symbol:
            raise HTTPException(status_code=422, detail="Invalid CoinGecko response")

        return ResolvedAsset(
            symbol=symbol,
            name=name,
            asset_type=AssetType.CRYPTOCURRENCY,
            market=Market.CRYPTO,
            currency=Currency.USD,
            country="GLOBAL",
            coingecko_id=cg_id,
        )
