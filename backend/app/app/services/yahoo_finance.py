"""
Yahoo Finance service for fetching stock and ETF prices.
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime

import httpx
import yfinance as yf

from app.models.asset import Currency, Market
from app.services.provider_errors import ProviderUnavailable

logger = logging.getLogger(__name__)


@dataclass
class StockPrice:
    """Price data returned from Yahoo Finance."""

    symbol: str
    price: float
    currency: Currency
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    change: float | None = None
    change_percent: float | None = None
    fetched_at: datetime = None

    def __post_init__(self):
        if self.fetched_at is None:
            self.fetched_at = datetime.utcnow()


class YahooFinanceService:
    """
    Service for fetching stock and ETF prices from Yahoo Finance.

    Handles:
    - US stocks (NYSE, NASDAQ): Use ticker as-is (e.g., "AAPL", "MSFT")
    - Mexican stocks (BMV): Append ".MX" suffix (e.g., "AMXL.MX", "FEMSAUBD.MX")
    """

    @staticmethod
    def get_yahoo_ticker(symbol: str, market: Market) -> str:
        """
        Convert symbol to Yahoo Finance ticker format.

        Args:
            symbol: The stock symbol
            market: The market where the stock trades

        Returns:
            Yahoo Finance compatible ticker
        """
        symbol = symbol.upper().strip()

        if market == Market.BMV:
            # Mexican stocks need .MX suffix
            if not symbol.endswith(".MX"):
                return f"{symbol}.MX"

        # US markets (NYSE, NASDAQ) use symbol as-is
        return symbol

    @classmethod
    async def get_price(
        cls, symbol: str, market: Market = Market.NYSE
    ) -> StockPrice | None:
        """
        Fetch current price for a stock or ETF.

        Args:
            symbol: Stock symbol
            market: Market where the stock trades

        Returns:
            StockPrice object or None if fetch fails
        """
        yahoo_ticker = cls.get_yahoo_ticker(symbol, market)

        try:
            ticker = yf.Ticker(yahoo_ticker)
            info = await asyncio.wait_for(
                asyncio.to_thread(lambda: ticker.info), timeout=8.0
            )

            if not info or "regularMarketPrice" not in info:
                # Try getting from history as fallback
                hist = await asyncio.wait_for(
                    asyncio.to_thread(ticker.history, period="1d"), timeout=8.0
                )
                if hist.empty:
                    logger.warning(f"No data available for {yahoo_ticker}")
                    raise ProviderUnavailable(
                        "Yahoo Finance returned no current price data"
                    )

                price = float(hist["Close"].iloc[-1])
                if not math.isfinite(price) or price <= 0:
                    raise ProviderUnavailable(
                        "Yahoo Finance returned an invalid current price"
                    )
                return StockPrice(
                    symbol=symbol,
                    price=price,
                    currency=Currency.MXN if market == Market.BMV else Currency.USD,
                )

            # Determine currency from Yahoo Finance data
            currency_str = info.get("currency", "USD")
            currency = Currency.MXN if currency_str == "MXN" else Currency.USD

            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if price is None:
                logger.warning(f"No price found for {yahoo_ticker}")
                raise ProviderUnavailable(
                    "Yahoo Finance returned no current price data"
                )
            price = float(price)
            if not math.isfinite(price) or price <= 0:
                raise ProviderUnavailable(
                    "Yahoo Finance returned an invalid current price"
                )

            # Calculate change
            previous_close = info.get("regularMarketPreviousClose")
            change = None
            change_percent = None
            if previous_close and price:
                change = price - previous_close
                change_percent = (change / previous_close) * 100

            return StockPrice(
                symbol=symbol,
                price=price,
                currency=currency,
                open_price=info.get("regularMarketOpen"),
                high_price=info.get("regularMarketDayHigh"),
                low_price=info.get("regularMarketDayLow"),
                previous_close=previous_close,
                volume=info.get("regularMarketVolume"),
                change=change,
                change_percent=change_percent,
            )

        except Exception as e:
            logger.error(f"Error fetching price for {yahoo_ticker}: {e}")
            raise ProviderUnavailable(
                "Yahoo Finance price service is unavailable"
            ) from e

    @classmethod
    async def get_prices_batch(
        cls, symbols: list[tuple[str, Market]]
    ) -> dict[str, StockPrice]:
        """
        Fetch prices for multiple stocks.

        Args:
            symbols: List of (symbol, market) tuples

        Returns:
            Dictionary mapping symbol to StockPrice
        """
        results = {}

        # Yahoo Finance doesn't have a great batch API for info,
        # so we fetch one at a time (could be optimized with download)
        for symbol, market in symbols:
            price = await cls.get_price(symbol, market)
            if price:
                results[symbol] = price

        return results

    @classmethod
    async def search_symbol(cls, query: str) -> list[dict]:
        """
        Search for stocks/ETFs by name or symbol.

        Note: This uses Yahoo Finance's search functionality
        which may have rate limits.
        """
        try:
            url = "https://query2.finance.yahoo.com/v1/finance/search"
            params = {
                "q": query,
                "quotesCount": 10,
                "newsCount": 0,
            }
            headers = {"User-Agent": "Mozilla/5.0"}

            timeout = httpx.Timeout(5.0, connect=3.0)
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False
            ) as client:
                response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

            results = []
            for quote in data.get("quotes", []):
                results.append(
                    {
                        "symbol": quote.get("symbol"),
                        "name": quote.get("longname") or quote.get("shortname"),
                        "type": quote.get("quoteType"),
                        "exchange": quote.get("exchange"),
                    }
                )

            return results

        except Exception as e:
            logger.error(f"Error searching for {query}: {e}")
            raise ProviderUnavailable("Yahoo Finance search is unavailable") from e
