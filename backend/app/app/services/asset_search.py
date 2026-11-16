"""Find investment assets at the market data providers (Yahoo Finance, CoinGecko).

Shared by the asset search endpoints and the chat/MCP asset search tool. The
results carry the ``provider`` and ``external_id`` that
``investment_ledger.record_transaction_with_asset`` resolves into an asset.
"""

from __future__ import annotations

from typing import Any

from app.models.asset import AssetType, Currency, Market
from app.schemas.asset import (
    ExternalAssetProvider,
    ExternalAssetSearchResult,
    ExternalCryptoSearchResult,
)
from app.services.coingecko import CoinGeckoService
from app.services.yahoo_finance import YahooFinanceService

# Stocks and ETFs from USA (NYSE, NASDAQ) and Mexican (BMV) markets.
_ALLOWED_EXCHANGES = frozenset(
    {"NYQ", "NMS", "NGM", "PCX", "BTS", "MEX", "NYSE", "NASDAQ"}
)
_ALLOWED_TYPES = frozenset({"EQUITY", "ETF"})


def _stock_result(item: dict[str, Any]) -> ExternalAssetSearchResult | None:
    raw_symbol = item.get("symbol")
    raw_name = item.get("name")
    if (
        not isinstance(raw_symbol, str)
        or not raw_symbol
        or len(raw_symbol) > 128
        or (raw_name is not None and not isinstance(raw_name, str))
        or (isinstance(raw_name, str) and len(raw_name) > 255)
    ):
        return None
    exchange = item.get("exchange", "")
    quote_type = item.get("type", "")
    if not isinstance(exchange, str) or len(exchange) > 32:
        return None

    is_mexican = raw_symbol.endswith(".MX")
    if not (exchange in _ALLOWED_EXCHANGES or is_mexican):
        return None
    if quote_type not in _ALLOWED_TYPES:
        return None

    if is_mexican or exchange == "MEX":
        market, currency, country = Market.BMV, Currency.MXN, "MX"
    elif exchange in {"NYQ", "NYSE"}:
        market, currency, country = Market.NYSE, Currency.USD, "US"
    else:
        market, currency, country = Market.NASDAQ, Currency.USD, "US"

    # The .MX suffix is not part of the stored symbol.
    symbol = raw_symbol.removesuffix(".MX")
    if not symbol or len(symbol) > 32:
        return None
    return ExternalAssetSearchResult(
        provider=ExternalAssetProvider.YAHOO,
        external_id=raw_symbol,
        symbol=symbol,
        name=raw_name or symbol,
        asset_type=AssetType.ETF if quote_type == "ETF" else AssetType.STOCK,
        market=market,
        currency=currency,
        country=country,
        exchange=exchange,
    )


async def search_stocks(query: str) -> list[ExternalAssetSearchResult]:
    """Stocks and ETFs on USA and Mexican exchanges matching ``query``."""
    results = await YahooFinanceService.search_symbol(query)
    return [r for item in results if (r := _stock_result(item)) is not None]


def _crypto_result(coin: dict[str, Any]) -> ExternalCryptoSearchResult | None:
    coin_id = coin.get("coingecko_id")
    symbol = coin.get("symbol")
    name = coin.get("name")
    if (
        not isinstance(coin_id, str)
        or not coin_id
        or len(coin_id) > 128
        or not isinstance(symbol, str)
        or not symbol
        or len(symbol) > 32
        or not isinstance(name, str)
        or not name
        or len(name) > 255
    ):
        return None
    return ExternalCryptoSearchResult(
        provider=ExternalAssetProvider.COINGECKO,
        external_id=coin_id,
        symbol=symbol,
        name=name,
        asset_type=AssetType.CRYPTOCURRENCY,
        market=Market.CRYPTO,
        currency=Currency.USD,
        coingecko_id=coin_id,
        market_cap_rank=coin.get("market_cap_rank"),
    )


async def search_crypto(query: str) -> list[ExternalCryptoSearchResult]:
    """Cryptocurrencies at CoinGecko matching ``query``."""
    results = await CoinGeckoService.search_coins(query)
    return [r for coin in results if (r := _crypto_result(coin)) is not None]
