"""
schemas/analytics.py — Pydantic response models for all analytics API endpoints.

These classes define the exact JSON shape FastAPI returns for:
    - Market overview (advancing/declining counts, average return)
    - Sector performance (grouped stats per sector)
    - Correlation matrix (pairwise Pearson correlation of daily returns)
    - Risk metrics time series (Sharpe ratio, Beta, Drawdown per ticker)
"""

from pydantic import BaseModel, ConfigDict   # BaseModel is the base class for all schemas; ConfigDict enables ORM mode
from typing import Optional, List, Dict      # Optional for nullable fields; List and Dict for collections
from datetime import date                    # date type for all trading-day date fields


# =============================================================================
# MARKET OVERVIEW SCHEMA
# Used by: GET /api/v1/analytics/market-overview
# Summarises the entire tracked market for the most recent trading day
# =============================================================================

class MarketOverviewOut(BaseModel):
    """High-level snapshot of all tracked stocks for the most recent trading day."""

    total_stocks: int                        # Total number of stocks that have a price entry for the latest date
    advancing: int                           # Number of stocks with a positive daily_return today
    declining: int                           # Number of stocks with a negative daily_return today
    unchanged: int                           # Number of stocks with zero or NULL daily_return today
    avg_return: Optional[float] = None       # Mean daily return across all tracked stocks — NULL if no data yet
    latest_date: Optional[date] = None       # The trading date this overview is calculated from


# =============================================================================
# SECTOR PERFORMANCE SCHEMAS
# Used by: GET /api/v1/analytics/sector-performance
# Groups all tracked stocks by their sector and summarises each sector
# =============================================================================

class SectorStockOut(BaseModel):
    """One stock's latest price snapshot — used as a line item inside a sector summary."""

    model_config = ConfigDict(from_attributes=True)  # Allow Pydantic to read values from SQLAlchemy ORM objects directly

    ticker: str                              # Stock ticker symbol (e.g. 'AAPL')
    company_name: Optional[str] = None       # Full company name — NULL until the stocks table is seeded
    close: Optional[float] = None            # Latest closing price for this stock
    daily_return: Optional[float] = None     # Latest daily return — positive means the stock gained today


class SectorPerformanceOut(BaseModel):
    """Aggregated performance summary for one market sector."""

    sector: str                              # Sector name (e.g. 'Technology', 'Healthcare', 'Finance')
    stock_count: int                         # Number of tracked stocks that belong to this sector
    avg_return: Optional[float] = None       # Mean daily return across all stocks in this sector — NULL if no return data
    best_ticker: Optional[str] = None        # Ticker with the highest daily return in this sector today
    worst_ticker: Optional[str] = None       # Ticker with the lowest daily return in this sector today
    stocks: List[SectorStockOut]             # Full list of stocks in this sector with their individual latest data


# =============================================================================
# CORRELATION MATRIX SCHEMA
# Used by: GET /api/v1/analytics/correlation?tickers=AAPL,MSFT&days=90
# Returns a pairwise Pearson correlation of daily returns between selected stocks
# =============================================================================

class CorrelationOut(BaseModel):
    """Pairwise Pearson return correlation matrix for the requested tickers."""

    tickers: List[str]                                        # The tickers included in this matrix (only those found in DB)
    days: int                                                 # Number of trading days of history used to compute the matrix
    matrix: Dict[str, Dict[str, Optional[float]]]             # Nested dict: matrix[tickerA][tickerB] = correlation (-1 to +1)


# =============================================================================
# RISK METRICS SCHEMAS
# Used by: GET /api/v1/analytics/{ticker}/risk
# Returns the historical time series of Sharpe, Beta, and Drawdown for one ticker
# =============================================================================

class RiskPointOut(BaseModel):
    """One day's risk metrics snapshot for a single ticker."""

    model_config = ConfigDict(from_attributes=True)  # Allow reading from SQLAlchemy ORM objects

    date: date                               # The trading date this snapshot corresponds to
    sharpe_ratio: Optional[float] = None     # Annualised Sharpe ratio on this date (NULL until 252 days of history exist)
    beta: Optional[float] = None             # Rolling 252-day Beta vs SPY (NULL until 252 days of history exist)
    drawdown: Optional[float] = None         # Drawdown from 252-day peak as a negative decimal (NULL until 252 days exist)


class RiskMetricsOut(BaseModel):
    """Full time series of risk metrics for a single ticker."""

    ticker: str                              # The stock ticker these risk metrics belong to
    count: int                               # Number of data points returned in this response
    data: List[RiskPointOut]                 # List of daily risk snapshots ordered most recent first
