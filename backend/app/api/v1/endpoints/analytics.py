"""
analytics.py — Analytics API endpoints.

All routes are served under /api/v1/analytics/ (prefix set in router.py).

Routes defined here:
    GET /analytics/market-overview       — Market-wide snapshot for the latest trading day
    GET /analytics/sector-performance    — Performance breakdown grouped by sector
    GET /analytics/correlation           — Pearson correlation matrix of daily returns
    GET /analytics/{ticker}/risk         — Historical Sharpe, Beta, Drawdown time series

CRITICAL ROUTE ORDER: Fixed-path routes (market-overview, sector-performance, correlation)
MUST be defined before /{ticker}/risk. FastAPI matches routes top-to-bottom and would
otherwise treat "market-overview" as a ticker value, returning a 404 instead of running
the correct function.
"""

from fastapi import APIRouter, Depends, HTTPException, Query   # APIRouter groups routes; Depends injects get_db; Query parses URL params
from sqlalchemy.ext.asyncio import AsyncSession                # Type hint for the async DB session injected by get_db
from sqlalchemy import select, func, and_, desc                # SQLAlchemy helpers: select builds queries, func calls SQL functions, and_ combines filters, desc sorts descending
from typing import List                                        # List type used in response_model declarations
import pandas as pd                                            # pandas is used to compute the correlation matrix from price data

from app.db.database import get_db                             # FastAPI dependency that opens and closes an async DB session per request
from app.models.stock import Stock, Price, Indicator           # ORM classes representing the stocks, prices, and indicators tables
from app.schemas.analytics import (                            # Import all Pydantic response schemas for this router
    MarketOverviewOut,                                         # Schema for the market-overview endpoint
    SectorPerformanceOut,                                      # Schema for the sector-performance endpoint
    SectorStockOut,                                            # Schema for one stock inside a sector summary
    CorrelationOut,                                            # Schema for the correlation matrix endpoint
    RiskMetricsOut,                                            # Schema for the risk metrics time series endpoint
    RiskPointOut,                                              # Schema for one data point in the risk metrics time series
)

router = APIRouter()                                           # Create the analytics router — this gets registered in router.py with prefix="/analytics"


# =============================================================================
# MARKET OVERVIEW
# GET /api/v1/analytics/market-overview
# Returns a market-wide snapshot: how many stocks advanced, declined, or were flat today
# =============================================================================

@router.get(
    "/market-overview",                                        # Fixed path — placed first so it is never mistaken for a ticker
    response_model=MarketOverviewOut,                          # FastAPI validates and serialises the return value using this schema
    summary="Market-wide snapshot for the latest trading day", # Short label shown in Swagger UI at /docs
)
async def market_overview(
    db: AsyncSession = Depends(get_db),                        # FastAPI injects a fresh async session for this request
):
    """
    Returns a high-level view of all tracked stocks for the most recent trading day.
    Shows how many stocks gained, lost, or were unchanged, plus the average market return.
    """
    latest_date_sq = (                                         # Subquery: find the most recent price date for each ticker
        select(
            Price.ticker,                                      # Include ticker so we can join back to prices
            func.max(Price.date).label("max_date")             # MAX(date) gives the most recent date per ticker
        )
        .group_by(Price.ticker)                                # One row per ticker — gets the latest date for each one
        .subquery()                                            # Wrap as subquery so it can be used in the main query's JOIN
    )

    stmt = (                                                   # Main query: get only the latest price row for each ticker
        select(Price.daily_return, Price.date)                 # We only need daily_return and date for the overview
        .join(latest_date_sq, and_(                            # Join to the subquery on both ticker AND date
            latest_date_sq.c.ticker == Price.ticker,           # Ticker must match
            latest_date_sq.c.max_date == Price.date            # Date must be the max date for that ticker
        ))
    )

    result = await db.execute(stmt)                            # Run the query asynchronously against the Supabase database
    rows = result.all()                                        # Fetch all rows — each row has .daily_return and .date

    if not rows:                                               # Database has no price data yet (pipeline not run)
        return MarketOverviewOut(                              # Return a zeroed-out overview rather than crashing
            total_stocks=0,
            advancing=0,
            declining=0,
            unchanged=0,
        )

    latest_date = rows[0].date                                 # Every row shares the same latest date — grab it from the first row
    returns = [                                                # Build a list of non-NULL daily returns for maths
        float(r.daily_return)
        for r in rows
        if r.daily_return is not None                          # Skip rows where daily_return is NULL (e.g. first trading day)
    ]

    advancing = sum(1 for r in returns if r > 0)              # Count stocks with a positive return today
    declining  = sum(1 for r in returns if r < 0)             # Count stocks with a negative return today
    unchanged  = len(rows) - advancing - declining             # Everything else: zero return or no return data

    avg_return = (                                             # Compute mean return across all non-null values
        sum(returns) / len(returns) if returns else None       # Guard against empty list (division by zero)
    )

    return MarketOverviewOut(                                  # Build and return the market overview response
        total_stocks=len(rows),                                # Total number of stocks with a price entry today
        advancing=advancing,                                   # Stocks that went up
        declining=declining,                                   # Stocks that went down
        unchanged=unchanged,                                   # Stocks that were flat or have no return data
        avg_return=avg_return,                                 # Market average return
        latest_date=latest_date,                               # The trading date this snapshot reflects
    )


# =============================================================================
# SECTOR PERFORMANCE
# GET /api/v1/analytics/sector-performance
# Groups all tracked stocks by their sector and returns aggregated stats per sector
# =============================================================================

@router.get(
    "/sector-performance",                                     # Fixed path — placed before /{ticker} to avoid mismatches
    response_model=List[SectorPerformanceOut],                 # Returns a list: one SectorPerformanceOut per sector
    summary="Latest performance breakdown grouped by market sector",
)
async def sector_performance(
    db: AsyncSession = Depends(get_db),                        # Inject async DB session
):
    """
    Returns a list of sectors, each with aggregated performance stats.
    For each sector: average return, best/worst stock ticker, and a breakdown of all stocks in it.
    """
    latest_date_sq = (                                         # Same subquery pattern: most recent date per ticker
        select(
            Price.ticker,
            func.max(Price.date).label("max_date")
        )
        .group_by(Price.ticker)
        .subquery()
    )

    stmt = (                                                   # Join stocks + prices + subquery to get one latest row per ticker
        select(Stock, Price)                                   # Select entire Stock and Price ORM objects so we can access all columns
        .join(Price, Price.ticker == Stock.ticker)             # Join prices to stocks on the ticker foreign key
        .join(latest_date_sq, and_(                            # Join subquery to filter to the latest price date only
            latest_date_sq.c.ticker == Price.ticker,
            latest_date_sq.c.max_date == Price.date
        ))
        .order_by(Stock.sector, Stock.ticker)                  # Sort by sector name, then ticker within each sector
    )

    result = await db.execute(stmt)                            # Execute the query
    rows = result.all()                                        # Each row is a (Stock, Price) tuple

    if not rows:                                               # No price data in the database yet
        return []                                              # Return an empty list — not an error

    sectors: dict = {}                                         # Dict to group stocks by sector: { sector_name: { ... } }

    for stock, price in rows:                                  # Iterate over each (Stock ORM object, Price ORM object) pair
        sector_name = stock.sector or "Unknown"                # Fall back to 'Unknown' if sector is NULL in the database

        if sector_name not in sectors:                         # First time we see this sector — initialise its bucket
            sectors[sector_name] = {
                "sector": sector_name,                         # Store sector name for the output object
                "stocks": [],                                  # List of SectorStockOut objects for this sector
                "returns": [],                                 # Temporary list of (ticker, return) tuples for computing avg/best/worst
            }

        stock_snapshot = SectorStockOut(                       # Build the per-stock snapshot object
            ticker=stock.ticker,                               # Ticker symbol
            company_name=stock.company_name,                   # Company name from stocks table
            close=float(price.close) if price.close is not None else None,               # Latest close price
            daily_return=float(price.daily_return) if price.daily_return is not None else None,  # Latest return
        )

        sectors[sector_name]["stocks"].append(stock_snapshot)  # Add this stock to its sector's list

        if price.daily_return is not None:                     # Only include non-NULL returns in the stats calculation
            sectors[sector_name]["returns"].append(            # Store (ticker, return) so we can rank them later
                (stock.ticker, float(price.daily_return))
            )

    output = []                                                # Final list of SectorPerformanceOut objects to return

    for sector_name, data in sorted(sectors.items()):         # Sort sectors alphabetically for a consistent response
        returns = data["returns"]                              # List of (ticker, return) tuples for this sector

        avg_return = (                                         # Mean return across all stocks in this sector
            sum(r for _, r in returns) / len(returns)
            if returns else None                               # NULL if no return data exists for this sector
        )

        best_ticker = (                                        # The ticker with the best (highest) return today
            max(returns, key=lambda x: x[1])[0]
            if returns else None
        )

        worst_ticker = (                                       # The ticker with the worst (lowest) return today
            min(returns, key=lambda x: x[1])[0]
            if returns else None
        )

        output.append(SectorPerformanceOut(                    # Build the sector summary object and add it to the output list
            sector=sector_name,                                # Sector name
            stock_count=len(data["stocks"]),                   # How many stocks are tracked in this sector
            avg_return=avg_return,                             # Average return across the sector
            best_ticker=best_ticker,                           # Top performer today
            worst_ticker=worst_ticker,                         # Worst performer today
            stocks=data["stocks"],                             # Full list of stocks with their individual snapshots
        ))

    return output                                              # Return all sectors sorted alphabetically


# =============================================================================
# CORRELATION MATRIX
# GET /api/v1/analytics/correlation?tickers=AAPL,MSFT,GOOGL&days=90
# Computes pairwise Pearson correlation of daily returns for selected tickers
# =============================================================================

@router.get(
    "/correlation",                                            # Fixed path — placed before /{ticker} routes
    response_model=CorrelationOut,                             # Returns a CorrelationOut schema object
    summary="Pearson correlation matrix of daily returns for selected tickers",
)
async def correlation_matrix(
    tickers: str = Query(                                      # Required query param: comma-separated list of tickers
        ...,                                                   # ... means this param is required — the request fails without it
        description="Comma-separated tickers, e.g. AAPL,MSFT,GOOGL. Max 10.",
    ),
    days: int = Query(                                         # Optional: how many trading days of history to use
        default=90,                                            # Default to 90 trading days (~4.5 months)
        ge=30,                                                 # Minimum 30 days — fewer gives unreliable correlations
        le=365,                                                # Maximum 365 days — one full trading year
        description="Number of trading days to include (30–365). Default: 90.",
    ),
    db: AsyncSession = Depends(get_db),                        # Inject async DB session
):
    """
    Returns a pairwise Pearson correlation matrix of daily returns.
    A value of +1 means stocks move together perfectly; -1 means they move in opposite directions.
    Useful for portfolio diversification analysis.
    """
    ticker_list = [                                            # Parse the tickers query param into a clean list
        t.strip().upper()                                      # Strip whitespace and uppercase each ticker
        for t in tickers.split(",")                            # Split on comma
    ]
    ticker_list = list(dict.fromkeys(ticker_list))             # Remove duplicates while preserving order
    ticker_list = ticker_list[:10]                             # Cap at 10 tickers — more than 10 makes the response unwieldy

    if len(ticker_list) < 2:                                   # Correlation needs at least 2 stocks to compare
        raise HTTPException(                                   # Return HTTP 400 Bad Request
            status_code=400,
            detail="At least 2 tickers are required to compute a correlation matrix.",
        )

    stmt = (                                                   # Fetch daily returns for the requested tickers
        select(Price.ticker, Price.date, Price.daily_return)   # Only these 3 columns are needed
        .where(Price.ticker.in_(ticker_list))                  # Filter to only the requested tickers
        .where(Price.daily_return.is_not(None))                # Skip rows where daily_return is NULL
        .order_by(Price.date.asc())                            # Ascending date so tail() gives the most recent N days
    )

    result = await db.execute(stmt)                            # Execute the query
    rows = result.all()                                        # List of Row objects, each with .ticker, .date, .daily_return

    if not rows:                                               # No matching data in the database
        raise HTTPException(
            status_code=404,
            detail="No price data found for the requested tickers.",
        )

    df = pd.DataFrame(                                         # Convert rows into a pandas DataFrame for matrix computation
        [
            (r.ticker, r.date, float(r.daily_return))          # Extract primitive values from each Row object
            for r in rows
        ],
        columns=["ticker", "date", "daily_return"],            # Name the three columns
    )

    pivot = df.pivot(                                          # Reshape: dates become the row index, tickers become column headers
        index="date",                                          # Each row = one trading date
        columns="ticker",                                      # Each column = one ticker's daily returns
        values="daily_return",                                 # Cell values = the return for that ticker on that date
    )

    pivot = pivot.tail(days)                                   # Keep only the most recent N trading days of data

    corr_matrix = pivot.corr(method="pearson")                 # Compute Pearson correlation between every pair of ticker columns

    matrix_dict = {}                                           # Convert pandas matrix to a plain Python dict-of-dicts for JSON output
    for ticker_a in corr_matrix.columns:                       # Iterate over each row ticker
        matrix_dict[ticker_a] = {}                             # Create a sub-dict for this ticker
        for ticker_b in corr_matrix.columns:                   # Iterate over each column ticker
            val = corr_matrix.loc[ticker_a, ticker_b]          # Look up the correlation value at row=tickerA, col=tickerB
            matrix_dict[ticker_a][ticker_b] = (               # Store rounded float or None if the value is NaN
                round(float(val), 4)
                if pd.notna(val)
                else None
            )

    found_tickers = list(corr_matrix.columns)                  # Tickers actually found in the database (subset of requested)

    return CorrelationOut(                                     # Build and return the correlation response
        tickers=found_tickers,                                 # The tickers included in the matrix
        days=days,                                             # How many days of history were used
        matrix=matrix_dict,                                    # The nested dict correlation matrix
    )


# =============================================================================
# RISK METRICS TIME SERIES
# GET /api/v1/analytics/{ticker}/risk
# Returns historical Sharpe ratio, Beta, and Drawdown for one ticker
# =============================================================================

@router.get(
    "/{ticker}/risk",                                          # Dynamic path — MUST be defined last so fixed paths above take priority
    response_model=RiskMetricsOut,                             # Returns a RiskMetricsOut schema object
    summary="Historical Sharpe ratio, Beta, and Drawdown for a single stock",
)
async def risk_metrics(
    ticker: str,                                               # Ticker extracted from the URL path segment
    limit: int = Query(                                        # Optional: how many rows to return
        default=252,                                           # Default to 252 rows = 1 full trading year
        ge=1,                                                  # Minimum 1 row
        le=2000,                                               # Maximum 2000 rows
        description="Number of rows to return (default: 252 = 1 trading year, max: 2000).",
    ),
    db: AsyncSession = Depends(get_db),                        # Inject async DB session
):
    """
    Returns the historical time series of Sharpe ratio, Beta, and Drawdown for the given ticker.
    These three values are NULL for the first 252 trading days because they require a full year
    of price history to compute a meaningful rolling window.
    """
    ticker = ticker.upper()                                    # Normalise to uppercase regardless of how the caller typed it

    stock = await db.get(Stock, ticker)                        # Look up the ticker in the stocks table using the primary key — fastest lookup
    if not stock:                                              # If the ticker does not exist in our tracked list
        raise HTTPException(                                   # Return HTTP 404 Not Found
            status_code=404,
            detail=f"Ticker '{ticker}' not found. Only tracked tickers are supported.",
        )

    stmt = (                                                   # Query the indicators table — only fetch the 4 columns we need
        select(
            Indicator.date,                                    # Trading date for this row
            Indicator.sharpe_ratio,                            # Annualised Sharpe ratio
            Indicator.beta,                                    # Rolling 252-day Beta vs SPY
            Indicator.drawdown,                                # Drawdown from 252-day peak
        )
        .where(Indicator.ticker == ticker)                     # Filter to the requested ticker only
        .where(                                                # Only return rows where at least one risk metric is non-NULL
            (Indicator.sharpe_ratio.is_not(None)) |            # Include if Sharpe ratio is available, OR
            (Indicator.beta.is_not(None)) |                    # Include if Beta is available, OR
            (Indicator.drawdown.is_not(None))                  # Include if Drawdown is available
        )
        .order_by(desc(Indicator.date))                        # Most recent trading day first
        .limit(limit)                                          # Respect the caller's requested row count
    )

    result = await db.execute(stmt)                            # Execute the query
    rows = result.all()                                        # List of Row objects

    data = [                                                   # Convert each Row into a RiskPointOut schema object
        RiskPointOut(
            date=row.date,                                     # Trading date for this data point
            sharpe_ratio=(                                     # Convert Decimal to float; keep None if NULL in DB
                float(row.sharpe_ratio) if row.sharpe_ratio is not None else None
            ),
            beta=(
                float(row.beta) if row.beta is not None else None
            ),
            drawdown=(
                float(row.drawdown) if row.drawdown is not None else None
            ),
        )
        for row in rows                                        # One RiskPointOut per database row
    ]

    return RiskMetricsOut(                                     # Build and return the full risk metrics response
        ticker=ticker,                                         # The ticker these metrics belong to
        count=len(data),                                       # How many data points are in the response
        data=data,                                             # Time series list, most recent date first
    )
