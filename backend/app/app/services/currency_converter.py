"""
Currency conversion service using Yahoo Finance for FX rates.
"""
import logging
import asyncio
import math
from datetime import datetime, timedelta
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


class CurrencyConverter:
    """
    Handles currency conversion between USD and MXN.
    
    Uses Yahoo Finance for real-time exchange rates.
    Caches rates to minimize API calls.
    """
    
    # Cache for exchange rates
    _rate_cache: dict[str, tuple[float, datetime]] = {}
    _cache_duration = timedelta(minutes=15)
    
    # Yahoo Finance ticker for USD/MXN
    USD_MXN_TICKER = "USDMXN=X"
    
    @classmethod
    async def get_usd_to_mxn_rate(cls) -> float:
        """Get current USD to MXN exchange rate."""
        cache_key = "USD_MXN"
        
        # Check cache
        if cache_key in cls._rate_cache:
            rate, cached_at = cls._rate_cache[cache_key]
            if (
                math.isfinite(rate)
                and 0 < rate <= 1e6
                and datetime.utcnow() - cached_at < cls._cache_duration
            ):
                return rate
            cls._rate_cache.pop(cache_key, None)
        
        try:
            ticker = yf.Ticker(cls.USD_MXN_TICKER)
            # Get the most recent price
            hist = await asyncio.wait_for(
                asyncio.to_thread(ticker.history, period="1d"), timeout=8.0
            )
            if not hist.empty:
                rate = float(hist["Close"].iloc[-1])
                if not math.isfinite(rate) or rate <= 0 or rate > 1e6:
                    raise ValueError("Yahoo returned an invalid USD/MXN exchange rate")
                cls._rate_cache[cache_key] = (rate, datetime.utcnow())
                logger.info(f"Fetched USD/MXN rate: {rate}")
                return rate
            else:
                logger.warning("No data returned for USD/MXN rate")
                fallback_rate = 17.0
                cls._rate_cache[cache_key] = (fallback_rate, datetime.utcnow())
                return fallback_rate
        except Exception as e:
            logger.error(f"Error fetching USD/MXN rate: {e}")
            # Return cached rate if available, otherwise fallback
            if cache_key in cls._rate_cache:
                return cls._rate_cache[cache_key][0]
            fallback_rate = 17.0
            cls._rate_cache[cache_key] = (fallback_rate, datetime.utcnow())
            return fallback_rate
    
    @classmethod
    async def get_mxn_to_usd_rate(cls) -> float:
        """Get current MXN to USD exchange rate."""
        usd_mxn = await cls.get_usd_to_mxn_rate()
        return 1.0 / usd_mxn
    
    @classmethod
    async def convert_usd_to_mxn(cls, amount: float) -> float:
        """Convert USD amount to MXN."""
        rate = await cls.get_usd_to_mxn_rate()
        return amount * rate
    
    @classmethod
    async def convert_mxn_to_usd(cls, amount: float) -> float:
        """Convert MXN amount to USD."""
        rate = await cls.get_mxn_to_usd_rate()
        return amount * rate
    
    @classmethod
    async def convert(
        cls, 
        amount: float, 
        from_currency: str, 
        to_currency: str
    ) -> float:
        """
        Convert amount between currencies.
        
        Args:
            amount: Amount to convert
            from_currency: Source currency (USD or MXN)
            to_currency: Target currency (USD or MXN)
        
        Returns:
            Converted amount
        """
        if from_currency == to_currency:
            return amount
        
        if from_currency == "USD" and to_currency == "MXN":
            return await cls.convert_usd_to_mxn(amount)
        elif from_currency == "MXN" and to_currency == "USD":
            return await cls.convert_mxn_to_usd(amount)
        else:
            raise ValueError(f"Unsupported currency pair: {from_currency}/{to_currency}")
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear the rate cache."""
        cls._rate_cache.clear()
