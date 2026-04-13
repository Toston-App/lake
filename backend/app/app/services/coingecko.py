"""
CoinGecko service for fetching cryptocurrency prices.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

from app.models.asset import Currency

logger = logging.getLogger(__name__)


# Common cryptocurrency symbol to CoinGecko ID mapping
CRYPTO_ID_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDT": "tether",
    "USDC": "usd-coin",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "SOL": "solana",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LTC": "litecoin",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "XLM": "stellar",
    "ALGO": "algorand",
    "NEAR": "near",
    "FTM": "fantom",
}


@dataclass
class CryptoPrice:
    """Price data returned from CoinGecko."""
    symbol: str
    coingecko_id: str
    price_usd: float
    price_mxn: float
    market_cap: Optional[float] = None
    volume_24h: Optional[float] = None
    change_24h: Optional[float] = None
    change_24h_percent: Optional[float] = None
    fetched_at: datetime = None
    
    def __post_init__(self):
        if self.fetched_at is None:
            self.fetched_at = datetime.utcnow()


class CoinGeckoService:
    """
    Service for fetching cryptocurrency prices from CoinGecko.
    
    Uses the free CoinGecko API (rate limited to ~50 calls/minute).
    """
    
    BASE_URL = "https://api.coingecko.com/api/v3"
    
    @staticmethod
    def get_coingecko_id(symbol: str) -> Optional[str]:
        """
        Convert crypto symbol to CoinGecko ID.
        
        Args:
            symbol: Cryptocurrency symbol (e.g., "BTC", "ETH")
        
        Returns:
            CoinGecko ID or None if not found
        """
        return CRYPTO_ID_MAP.get(symbol.upper())

    @classmethod
    async def get_price(
        cls,
        symbol: str,
        coingecko_id: Optional[str] = None,
    ) -> Optional[CryptoPrice]:
        """
        Fetch current price for a cryptocurrency.

        Args:
            symbol: Cryptocurrency symbol (e.g., "BTC", "ETH")
            coingecko_id: CoinGecko ID (preferred when available)

        Returns:
            CryptoPrice object or None if fetch fails
        """
        resolved_coingecko_id = coingecko_id or cls.get_coingecko_id(symbol)
        if not resolved_coingecko_id:
            #TODO: improve this
            logger.warning(f"Unknown cryptocurrency symbol: {symbol}")
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{cls.BASE_URL}/simple/price",
                    params={
                        "ids": resolved_coingecko_id,
                        "vs_currencies": "usd,mxn",
                        "include_24hr_change": "true",
                        "include_24hr_vol": "true",
                        "include_market_cap": "true",
                    },
                )
                response.raise_for_status()
                data = response.json()

                if resolved_coingecko_id not in data:
                    logger.warning(f"No data returned for {resolved_coingecko_id}")
                    return None

                coin_data = data[resolved_coingecko_id]
                price_usd = coin_data.get("usd")

                if price_usd is None:
                    return None

                return CryptoPrice(
                    symbol=symbol.upper(),
                    coingecko_id=resolved_coingecko_id,
                    price_usd=price_usd,
                    #TODO: improve this conversion (fetch actual MXN price with CurrencyConverter)
                    price_mxn=coin_data.get("mxn", price_usd * 17),  # Fallback conversion
                    market_cap=coin_data.get("usd_market_cap"),
                    volume_24h=coin_data.get("usd_24h_vol"),
                    change_24h_percent=coin_data.get("usd_24h_change"),
                )

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching {symbol} price: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching {symbol} price: {e}")
            return None

    @classmethod
    async def get_prices_batch(cls, symbols: list[str]) -> dict[str, CryptoPrice]:
        """
        Fetch prices for multiple cryptocurrencies in a single request.
        
        Args:
            symbols: List of cryptocurrency symbols
        
        Returns:
            Dictionary mapping symbol to CryptoPrice
        """
        # Convert symbols to CoinGecko IDs
        id_to_symbol = {}
        ids = []
        for symbol in symbols:
            cg_id = cls.get_coingecko_id(symbol)
            if cg_id:
                ids.append(cg_id)
                id_to_symbol[cg_id] = symbol
        
        if not ids:
            return {}
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{cls.BASE_URL}/simple/price",
                    params={
                        "ids": ",".join(ids),
                        "vs_currencies": "usd,mxn",
                        "include_24hr_change": "true",
                        "include_24hr_vol": "true",
                        "include_market_cap": "true",
                    },
                )
                response.raise_for_status()
                data = response.json()
                
                results = {}
                for cg_id, coin_data in data.items():
                    symbol = id_to_symbol.get(cg_id)
                    if symbol and "usd" in coin_data:
                        results[symbol] = CryptoPrice(
                            symbol=symbol,
                            coingecko_id=cg_id,
                            price_usd=coin_data["usd"],
                            price_mxn=coin_data.get("mxn", coin_data["usd"] * 17),
                            market_cap=coin_data.get("usd_market_cap"),
                            volume_24h=coin_data.get("usd_24h_vol"),
                            change_24h_percent=coin_data.get("usd_24h_change"),
                        )
                
                return results
                
        except Exception as e:
            logger.error(f"Error fetching batch crypto prices: {e}")
            return {}
    
    @classmethod
    async def search_coins(cls, query: str) -> list[dict]:
        """
        Search for cryptocurrencies by name or symbol.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{cls.BASE_URL}/search",
                    params={"query": query},
                )
                response.raise_for_status()
                data = response.json()
                
                results = []
                for coin in data.get("coins", [])[:10]:
                    results.append({
                        "symbol": coin.get("symbol", "").upper(),
                        "name": coin.get("name"),
                        "coingecko_id": coin.get("id"),
                        "market_cap_rank": coin.get("market_cap_rank"),
                    })
                
                return results
                
        except Exception as e:
            logger.error(f"Error searching coins: {e}")
            return []
    
    @classmethod
    def add_symbol_mapping(cls, symbol: str, coingecko_id: str) -> None:
        """
        Add a custom symbol to CoinGecko ID mapping.
        
        Args:
            symbol: Cryptocurrency symbol
            coingecko_id: CoinGecko ID
        """
        CRYPTO_ID_MAP[symbol.upper()] = coingecko_id
