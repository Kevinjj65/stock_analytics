"""
schemas/stock.py — Pydantic response models for all stock-related API endpoints.

These classes define exactly what JSON shape FastAPI returns for each endpoint.
They also validate the data coming out of the database before sending it to the client.
'from_attributes=True' enables reading values directly from SQLAlchemy ORM objects.
"""

from pydantic import BaseModel, ConfigDict     # BaseModel is the base class for all schemas; ConfigDict configures ORM mode
from typing import Optional, List              # Optional for nullable fields; List for array responses
from datetime import date, datetime            # date for trading-day columns; datetime for timestamptz columns


# =============================================================================
# STOCK SCHEMA
# Used by: GET /stocks/, GET /stocks/{ticker}
# Maps to: the Stock ORM model / stocks table
# =============================================================================

class StockOut(BaseModel):
    """Response schema for a single stock's metadata."""
    model_config = ConfigDict(from_attributes=True)  # Allows Pydantic to read values from SQLAlchemy ORM objects directly

    ticker:       str                                # Stock ticker symbol — the primary key (e.g. 'AAPL')
    company_name: Optional[str]   = None             # Full company name — nullable until populated (e.g. 'Apple Inc.')
    sector:       Optional[str]   = None             # Business sector — nullable (e.g. 'Technology')
    market_cap:   Optional[int]   = None             # Market capitalisation in USD — nullable BigInteger
    exchange:     Optional[str]   = None             # Exchange name — nullable (e.g. 'NASDAQ')
    created_at:   Optional[datetime] = None          # Timestamp of when this record was first inserted


# =============================================================================
# PRICE SCHEMA
# Used by: GET /stocks/{ticker}/prices
# Maps to: the Price ORM model / prices table
# =============================================================================

class PriceOut(BaseModel):
    """Response schema for a single day's OHLCV price data."""
    model_config = ConfigDict(from_attributes=True)  # Enable ORM object reading

    ticker:       str                                # Stock ticker this price row belongs to
    date:         date                               # Trading date (calendar date, no time component)
    open:         Optional[float] = None             # Opening price — nullable in case yfinance returned NaN
    high:         Optional[float] = None             # Highest price of the day — nullable
    low:          Optional[float] = None             # Lowest price of the day — nullable
    close:        Optional[float] = None             # Closing price — nullable (most important value)
    volume:       Optional[int]   = None             # Total shares traded — nullable BigInteger
    daily_return: Optional[float] = None             # Percentage return vs previous day — nullable (NULL for first row)


class PriceListOut(BaseModel):
    """Wrapper response for a list of price rows, including metadata."""
    model_config = ConfigDict(from_attributes=True)  # Enable ORM object reading

    ticker:  str                                     # Ticker symbol so the caller doesn't need to inspect individual rows
    count:   int                                     # Number of rows returned in this response
    prices:  List[PriceOut]                          # Ordered list of daily OHLCV rows (most recent first)


# =============================================================================
# INDICATOR SCHEMA
# Used by: GET /stocks/{ticker}/indicators
# Maps to: the Indicator ORM model / indicators table
# =============================================================================

class IndicatorOut(BaseModel):
    """Response schema for one day's computed technical indicators."""
    model_config = ConfigDict(from_attributes=True)  # Enable ORM object reading

    ticker:           str                            # Stock ticker this indicator row belongs to
    date:             date                           # Trading date these indicators correspond to

    # Core moving averages and momentum indicators — available after 50+ days of price history
    sma_20:           Optional[float] = None         # Simple Moving Average (20-day): average of last 20 closing prices
    sma_50:           Optional[float] = None         # Simple Moving Average (50-day): longer-term trend signal
    ema_20:           Optional[float] = None         # Exponential Moving Average (20-day): weighted, more responsive to recent price
    rsi_14:           Optional[float] = None         # Relative Strength Index: 0-100 scale, >70 overbought, <30 oversold

    # MACD — trend and momentum crossover signal
    macd:             Optional[float] = None         # MACD line: EMA-12 minus EMA-26
    macd_signal:      Optional[float] = None         # Signal line: 9-day EMA of the MACD line

    # Bollinger Bands — volatility envelope around price
    bollinger_upper:  Optional[float] = None         # Upper band: SMA-20 + 2 standard deviations
    bollinger_lower:  Optional[float] = None         # Lower band: SMA-20 - 2 standard deviations
    bollinger_mid:    Optional[float] = None         # Midline: same as SMA-20

    # Risk and performance metrics — available only after 252+ days of history
    sharpe_ratio:     Optional[float] = None         # Annualised Sharpe ratio: risk-adjusted return (NULL until 252 days of data)
    beta:             Optional[float] = None         # Rolling Beta vs SPY: market sensitivity (NULL until 252 days of data)
    drawdown:         Optional[float] = None         # Current drawdown from 252-day peak: negative decimal (NULL until 252 days)


class IndicatorListOut(BaseModel):
    """Wrapper response for a list of indicator rows."""
    model_config = ConfigDict(from_attributes=True)  # Enable ORM object reading

    ticker:     str                                  # Ticker symbol for this batch of indicators
    count:      int                                  # Number of indicator rows returned
    indicators: List[IndicatorOut]                   # List of daily indicator rows, most recent first


# =============================================================================
# TOP MOVERS SCHEMA
# Used by: GET /stocks/top-movers
# Combines stock metadata with the latest price and daily return
# =============================================================================

class TopMoverOut(BaseModel):
    """Response schema for a single entry in the top movers list."""
    model_config = ConfigDict(from_attributes=True)  # Enable ORM object reading

    ticker:       str                                # Stock ticker symbol
    company_name: Optional[str]   = None             # Company name for display in the UI
    sector:       Optional[str]   = None             # Sector for colour-coding in the dashboard
    date:         date                               # The trading date of the latest price row
    close:        Optional[float] = None             # Latest closing price
    daily_return: Optional[float] = None             # Latest daily return — what the sort is based on


class TopMoversOut(BaseModel):
    """Wrapper holding both the top gainers and top losers lists."""

    gainers: List[TopMoverOut]                       # Top N stocks with the highest (most positive) daily return
    losers:  List[TopMoverOut]                       # Top N stocks with the lowest (most negative) daily return


# =============================================================================
# COMPARE SCHEMA
# Used by: GET /stocks/compare?tickers=AAPL,MSFT,GOOGL
# Returns the latest price and latest indicator values for each requested ticker
# =============================================================================

class CompareItemOut(BaseModel):
    """Latest snapshot (price + indicators) for one stock in a comparison."""
    model_config = ConfigDict(from_attributes=True)  # Enable ORM object reading

    ticker:       str                                # Ticker symbol
    company_name: Optional[str]   = None             # Company name for UI labels
    sector:       Optional[str]   = None             # Sector for grouping

    # Latest price snapshot
    latest_date:  Optional[date]  = None             # Most recent trading date available in the database
    close:        Optional[float] = None             # Latest closing price
    daily_return: Optional[float] = None             # Latest daily return

    # Latest indicator snapshot (NULL if fewer than 50 days of history)
    sma_20:       Optional[float] = None             # Latest SMA-20 value
    sma_50:       Optional[float] = None             # Latest SMA-50 value
    rsi_14:       Optional[float] = None             # Latest RSI-14 value
    macd:         Optional[float] = None             # Latest MACD line value
    macd_signal:  Optional[float] = None             # Latest MACD signal line value


class CompareOut(BaseModel):
    """Response for the compare endpoint: a dict of ticker → snapshot."""

    requested_tickers: List[str]                     # The tickers the client asked for, in the order they were requested
    results: dict                                    # Dict mapping ticker string → CompareItemOut object
