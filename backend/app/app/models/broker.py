"""
Broker definitions for investment transactions.

This module defines a predefined list of popular brokers across different
regions (US, Mexico, International) and types (Traditional, Discount, Crypto).
"""
import enum
from dataclasses import dataclass
from typing import Optional


class BrokerType(str, enum.Enum):
    """Type of brokerage."""
    TRADITIONAL = "traditional"   # Full-service brokers
    DISCOUNT = "discount"         # Low-cost/commission-free brokers
    CRYPTO = "crypto"             # Cryptocurrency exchanges
    BANK = "bank"                 # Bank-affiliated brokers


class BrokerCountry(str, enum.Enum):
    """Primary country/region of operation."""
    US = "US"
    MX = "MX"
    INTERNATIONAL = "INTERNATIONAL"


@dataclass
class BrokerInfo:
    """Metadata for a broker."""
    code: str
    name: str
    country: BrokerCountry
    broker_type: BrokerType
    website: Optional[str] = None
    logo_url: Optional[str] = None


class Broker(str, enum.Enum):
    """Predefined list of supported brokers."""
    # US Traditional
    FIDELITY = "FIDELITY"
    SCHWAB = "SCHWAB"
    VANGUARD = "VANGUARD"
    ETRADE = "ETRADE"
    TD_AMERITRADE = "TD_AMERITRADE"
    MERRILL = "MERRILL"
    MORGAN_STANLEY = "MORGAN_STANLEY"
    JP_MORGAN = "JP_MORGAN"
    
    # US Discount
    ROBINHOOD = "ROBINHOOD"
    WEBULL = "WEBULL"
    SOFI = "SOFI"
    M1_FINANCE = "M1_FINANCE"
    ALLY = "ALLY"
    TRADESTATION = "TRADESTATION"
    FIRSTRADE = "FIRSTRADE"
    PUBLIC = "PUBLIC"
    TASTYTRADE = "TASTYTRADE"
    
    # Mexico
    GBM = "GBM"
    KUSPIT = "KUSPIT"
    ACTINVER = "ACTINVER"
    BURSANET = "BURSANET"
    BBVA_TRADER = "BBVA_TRADER"
    FINAMEX = "FINAMEX"
    BANORTE = "BANORTE"
    VECTOR = "VECTOR"
    INTERCAM = "INTERCAM"
    MONEX = "MONEX"
    
    # Crypto
    COINBASE = "COINBASE"
    BINANCE = "BINANCE"
    KRAKEN = "KRAKEN"
    GEMINI = "GEMINI"
    CRYPTO_COM = "CRYPTO_COM"
    BITSO = "BITSO"
    KUCOIN = "KUCOIN"
    BITSTAMP = "BITSTAMP"
    OKX = "OKX"
    BYBIT = "BYBIT"
    
    # International
    IBKR = "IBKR"
    DEGIRO = "DEGIRO"
    ETORO = "ETORO"
    TRADING_212 = "TRADING_212"
    SAXO = "SAXO"
    SWISSQUOTE = "SWISSQUOTE"
    CMC = "CMC"
    IG = "IG"


# Complete broker metadata with logos (using Clearbit Logo API)
BROKER_INFO: dict[Broker, BrokerInfo] = {
    # ===================
    # US Traditional
    # ===================
    Broker.FIDELITY: BrokerInfo(
        code="FIDELITY",
        name="Fidelity Investments",
        country=BrokerCountry.US,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.fidelity.com",
        logo_url="https://logo.clearbit.com/fidelity.com"
    ),
    Broker.SCHWAB: BrokerInfo(
        code="SCHWAB",
        name="Charles Schwab",
        country=BrokerCountry.US,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.schwab.com",
        logo_url="https://logo.clearbit.com/schwab.com"
    ),
    Broker.VANGUARD: BrokerInfo(
        code="VANGUARD",
        name="Vanguard",
        country=BrokerCountry.US,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.vanguard.com",
        logo_url="https://logo.clearbit.com/vanguard.com"
    ),
    Broker.ETRADE: BrokerInfo(
        code="ETRADE",
        name="E*TRADE",
        country=BrokerCountry.US,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.etrade.com",
        logo_url="https://logo.clearbit.com/etrade.com"
    ),
    Broker.TD_AMERITRADE: BrokerInfo(
        code="TD_AMERITRADE",
        name="TD Ameritrade",
        country=BrokerCountry.US,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.tdameritrade.com",
        logo_url="https://logo.clearbit.com/tdameritrade.com"
    ),
    Broker.MERRILL: BrokerInfo(
        code="MERRILL",
        name="Merrill Edge",
        country=BrokerCountry.US,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.merrilledge.com",
        logo_url="https://logo.clearbit.com/merrilledge.com"
    ),
    Broker.MORGAN_STANLEY: BrokerInfo(
        code="MORGAN_STANLEY",
        name="Morgan Stanley",
        country=BrokerCountry.US,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.morganstanley.com",
        logo_url="https://logo.clearbit.com/morganstanley.com"
    ),
    Broker.JP_MORGAN: BrokerInfo(
        code="JP_MORGAN",
        name="J.P. Morgan",
        country=BrokerCountry.US,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.jpmorgan.com",
        logo_url="https://logo.clearbit.com/jpmorgan.com"
    ),
    
    # ===================
    # US Discount
    # ===================
    Broker.ROBINHOOD: BrokerInfo(
        code="ROBINHOOD",
        name="Robinhood",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.robinhood.com",
        logo_url="https://logo.clearbit.com/robinhood.com"
    ),
    Broker.WEBULL: BrokerInfo(
        code="WEBULL",
        name="Webull",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.webull.com",
        logo_url="https://logo.clearbit.com/webull.com"
    ),
    Broker.SOFI: BrokerInfo(
        code="SOFI",
        name="SoFi Invest",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.sofi.com",
        logo_url="https://logo.clearbit.com/sofi.com"
    ),
    Broker.M1_FINANCE: BrokerInfo(
        code="M1_FINANCE",
        name="M1 Finance",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.m1finance.com",
        logo_url="https://logo.clearbit.com/m1finance.com"
    ),
    Broker.ALLY: BrokerInfo(
        code="ALLY",
        name="Ally Invest",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.ally.com/invest",
        logo_url="https://logo.clearbit.com/ally.com"
    ),
    Broker.TRADESTATION: BrokerInfo(
        code="TRADESTATION",
        name="TradeStation",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.tradestation.com",
        logo_url="https://logo.clearbit.com/tradestation.com"
    ),
    Broker.FIRSTRADE: BrokerInfo(
        code="FIRSTRADE",
        name="Firstrade",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.firstrade.com",
        logo_url="https://logo.clearbit.com/firstrade.com"
    ),
    Broker.PUBLIC: BrokerInfo(
        code="PUBLIC",
        name="Public.com",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.public.com",
        logo_url="https://logo.clearbit.com/public.com"
    ),
    Broker.TASTYTRADE: BrokerInfo(
        code="TASTYTRADE",
        name="tastytrade",
        country=BrokerCountry.US,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.tastytrade.com",
        logo_url="https://logo.clearbit.com/tastytrade.com"
    ),
    
    # ===================
    # Mexico
    # ===================
    Broker.GBM: BrokerInfo(
        code="GBM",
        name="GBM+",
        country=BrokerCountry.MX,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.gbm.com",
        logo_url="https://logo.clearbit.com/gbm.com"
    ),
    Broker.KUSPIT: BrokerInfo(
        code="KUSPIT",
        name="Kuspit",
        country=BrokerCountry.MX,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.kuspit.com",
        logo_url="https://logo.clearbit.com/kuspit.com"
    ),
    Broker.ACTINVER: BrokerInfo(
        code="ACTINVER",
        name="Actinver",
        country=BrokerCountry.MX,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.actinver.com",
        logo_url="https://logo.clearbit.com/actinver.com"
    ),
    Broker.BURSANET: BrokerInfo(
        code="BURSANET",
        name="Bursanet",
        country=BrokerCountry.MX,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.bursanet.mx",
        logo_url="https://logo.clearbit.com/bursanet.mx"
    ),
    Broker.BBVA_TRADER: BrokerInfo(
        code="BBVA_TRADER",
        name="BBVA Trader",
        country=BrokerCountry.MX,
        broker_type=BrokerType.BANK,
        website="https://www.bbva.mx",
        logo_url="https://logo.clearbit.com/bbva.mx"
    ),
    Broker.FINAMEX: BrokerInfo(
        code="FINAMEX",
        name="Finamex",
        country=BrokerCountry.MX,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.finamex.com.mx",
        logo_url="https://logo.clearbit.com/finamex.com.mx"
    ),
    Broker.BANORTE: BrokerInfo(
        code="BANORTE",
        name="Banorte",
        country=BrokerCountry.MX,
        broker_type=BrokerType.BANK,
        website="https://www.banorte.com",
        logo_url="https://logo.clearbit.com/banorte.com"
    ),
    Broker.VECTOR: BrokerInfo(
        code="VECTOR",
        name="Vector Casa de Bolsa",
        country=BrokerCountry.MX,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.vector.com.mx",
        logo_url="https://logo.clearbit.com/vector.com.mx"
    ),
    Broker.INTERCAM: BrokerInfo(
        code="INTERCAM",
        name="Intercam",
        country=BrokerCountry.MX,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.intercam.com.mx",
        logo_url="https://logo.clearbit.com/intercam.com.mx"
    ),
    Broker.MONEX: BrokerInfo(
        code="MONEX",
        name="Monex",
        country=BrokerCountry.MX,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.monex.com.mx",
        logo_url="https://logo.clearbit.com/monex.com.mx"
    ),
    
    # ===================
    # Crypto
    # ===================
    Broker.COINBASE: BrokerInfo(
        code="COINBASE",
        name="Coinbase",
        country=BrokerCountry.US,
        broker_type=BrokerType.CRYPTO,
        website="https://www.coinbase.com",
        logo_url="https://logo.clearbit.com/coinbase.com"
    ),
    Broker.BINANCE: BrokerInfo(
        code="BINANCE",
        name="Binance",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.CRYPTO,
        website="https://www.binance.com",
        logo_url="https://logo.clearbit.com/binance.com"
    ),
    Broker.KRAKEN: BrokerInfo(
        code="KRAKEN",
        name="Kraken",
        country=BrokerCountry.US,
        broker_type=BrokerType.CRYPTO,
        website="https://www.kraken.com",
        logo_url="https://logo.clearbit.com/kraken.com"
    ),
    Broker.GEMINI: BrokerInfo(
        code="GEMINI",
        name="Gemini",
        country=BrokerCountry.US,
        broker_type=BrokerType.CRYPTO,
        website="https://www.gemini.com",
        logo_url="https://logo.clearbit.com/gemini.com"
    ),
    Broker.CRYPTO_COM: BrokerInfo(
        code="CRYPTO_COM",
        name="Crypto.com",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.CRYPTO,
        website="https://www.crypto.com",
        logo_url="https://logo.clearbit.com/crypto.com"
    ),
    Broker.BITSO: BrokerInfo(
        code="BITSO",
        name="Bitso",
        country=BrokerCountry.MX,
        broker_type=BrokerType.CRYPTO,
        website="https://www.bitso.com",
        logo_url="https://logo.clearbit.com/bitso.com"
    ),
    Broker.KUCOIN: BrokerInfo(
        code="KUCOIN",
        name="KuCoin",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.CRYPTO,
        website="https://www.kucoin.com",
        logo_url="https://logo.clearbit.com/kucoin.com"
    ),
    Broker.BITSTAMP: BrokerInfo(
        code="BITSTAMP",
        name="Bitstamp",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.CRYPTO,
        website="https://www.bitstamp.net",
        logo_url="https://logo.clearbit.com/bitstamp.net"
    ),
    Broker.OKX: BrokerInfo(
        code="OKX",
        name="OKX",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.CRYPTO,
        website="https://www.okx.com",
        logo_url="https://logo.clearbit.com/okx.com"
    ),
    Broker.BYBIT: BrokerInfo(
        code="BYBIT",
        name="Bybit",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.CRYPTO,
        website="https://www.bybit.com",
        logo_url="https://logo.clearbit.com/bybit.com"
    ),
    
    # ===================
    # International
    # ===================
    Broker.IBKR: BrokerInfo(
        code="IBKR",
        name="Interactive Brokers",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.interactivebrokers.com",
        logo_url="https://logo.clearbit.com/interactivebrokers.com"
    ),
    Broker.DEGIRO: BrokerInfo(
        code="DEGIRO",
        name="DEGIRO",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.degiro.com",
        logo_url="https://logo.clearbit.com/degiro.com"
    ),
    Broker.ETORO: BrokerInfo(
        code="ETORO",
        name="eToro",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.etoro.com",
        logo_url="https://logo.clearbit.com/etoro.com"
    ),
    Broker.TRADING_212: BrokerInfo(
        code="TRADING_212",
        name="Trading 212",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.DISCOUNT,
        website="https://www.trading212.com",
        logo_url="https://logo.clearbit.com/trading212.com"
    ),
    Broker.SAXO: BrokerInfo(
        code="SAXO",
        name="Saxo Bank",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.saxobank.com",
        logo_url="https://logo.clearbit.com/saxobank.com"
    ),
    Broker.SWISSQUOTE: BrokerInfo(
        code="SWISSQUOTE",
        name="Swissquote",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.swissquote.com",
        logo_url="https://logo.clearbit.com/swissquote.com"
    ),
    Broker.CMC: BrokerInfo(
        code="CMC",
        name="CMC Markets",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.cmcmarkets.com",
        logo_url="https://logo.clearbit.com/cmcmarkets.com"
    ),
    Broker.IG: BrokerInfo(
        code="IG",
        name="IG",
        country=BrokerCountry.INTERNATIONAL,
        broker_type=BrokerType.TRADITIONAL,
        website="https://www.ig.com",
        logo_url="https://logo.clearbit.com/ig.com"
    ),
}


def get_broker_info(broker: Broker) -> BrokerInfo:
    """Get metadata for a broker."""
    return BROKER_INFO[broker]


def get_all_brokers() -> list[BrokerInfo]:
    """Get all broker metadata as a list."""
    return list(BROKER_INFO.values())


def search_brokers(query: str) -> list[BrokerInfo]:
    """Search brokers by name or code."""
    query_lower = query.lower()
    return [
        info for info in BROKER_INFO.values()
        if query_lower in info.code.lower() or query_lower in info.name.lower()
    ]
