"""
endpoints/stocks.py — All stock-related API route handlers.

Routes defined here:
    GET /api/v1/stocks/                         → list all tracked stocks
    GET /api/v1/stocks/top-movers               → top gainers and losers by daily return
    GET /api/v1/stocks/compare                  → side-by-side snapshot for multiple tickers
    GET /api/v1/stocks/{ticker}                 → metadata for one stock
    GET /api/v1/stocks/{ticker}/prices          → OHLCV price history
    GET /api/v1/stocks/{ticker}/indicators      → technical indicators history

IMPORTANT: Fixed-path routes (/top-movers, /compare) MUST be defined before
the dynamic /{ticker} route, otherwise FastAPI matches them as ticker values.
"""

from fastapi import APIRouter, Depends, HTTPException, Query   # APIRouter groups related routes; Depends injects the DB session; HTTPException returns error responses; Query defines query parameters
from sqlalchemy.ext.asyncio import AsyncSession                # AsyncSession is the async database session type used in all queries
from sqlalchemy import select, desc, func                      # select builds SELECT queries; desc sorts descending; func provides SQL functions like MAX()
from typing import List, Optional                              # List for response arrays; Optional for nullable query params

from app.db.database import get_db                            # FastAPI dependency that opens and closes a DB session per request
from app.models.stock import Stock, Price, Indicator          # SQLAlchemy ORM models mapping to the stocks, prices, and indicators tables
from app.schemas.stock import (                               # Pydantic response schemas that define the JSON shape returned to the client
    StockOut,                                                 # Schema for a single stock's metadata
    PriceOut,                                                 # Schema for a single price row
    PriceListOut,                                             # Wrapper schema for a list of price rows
    IndicatorOut,                                             # Schema for a single indicator row
    IndicatorListOut,                                         # Wrapper schema for a list of indicator rows
    TopMoverOut,                                              # Schema for one entry in the top-movers list
    TopMoversOut,                                             # Wrapper holding gainers and losers lists
    CompareItemOut,                                           # Schema for one ticker's snapshot in the compare response
    CompareOut,                                               # Wrapper schema for the full compare response
)


# ---------------------------------------------------------------------------
# ROUTER INSTANCE
# All routes in this file are registered on this router object.
# The prefix (/stocks) and tag (Stocks) are applied in router.py when this
# router is included into the main v1 router.
# ---------------------------------------------------------------------------

router = APIRouter()                                           # Create the router — routes defined below attach to this object


# =============================================================================
# GET /stocks/
# Returns metadata for all 21 tracked stocks (20 equities + SPY).
# =============================================================================

@router.get(                                                   # Register a GET handler on this router
    "/",                                                       # Path relative to the router's prefix — full path is /api/v1/stocks/
    response_model=List[StockOut],                             # FastAPI validates the return value against this Pydantic schema
    summary="List all tracked stocks",                         # Short description shown in Swagger UI
    description="Returns metadata (company name, sector, exchange) for all 21 tracked stocks.",  # Longer description in Swagger UI
)
async def list_stocks(
    db: AsyncSession = Depends(get_db),                        # Inject the async database session via the get_db dependency
) -> List[StockOut]:                                           # Return type hint — list of StockOut objects
    """Fetches all rows from the stocks table ordered alphabetically by ticker."""

    stmt = (                                                   # Build the SELECT query using SQLAlchemy's query builder
        select(Stock)                                          # SELECT * FROM stocks
        .order_by(Stock.ticker)                                # ORDER BY ticker ASC — alphabetical order for consistent output
    )
    result = await db.execute(stmt)                            # Execute the query asynchronously against Supabase PostgreSQL
    stocks = result.scalars().all()                            # Extract all Stock ORM objects from the result set
    return stocks                                              # FastAPI serialises the ORM objects using the StockOut schema


# =============================================================================
# GET /stocks/top-movers
# Returns the top N gainers and top N losers by today's daily_return.
# MUST be defined before /{ticker} so FastAPI does not treat 'top-movers' as a ticker.
# =============================================================================

@router.get(
    "/top-movers",                                             # Fixed path — must come before /{ticker} in the file
    response_model=TopMoversOut,                               # Response is a TopMoversOut wrapper with gainers and losers lists
    summary="Top daily gainers and losers",
    description=(
        "Returns the top N stocks with the highest and lowest daily return "
        "based on the most recent trading day available in the database."
    ),
)
async def top_movers(
    n: int = Query(default=5, ge=1, le=20, description="Number of gainers and losers to return (max 20)"),  # Query param ?n=5 — how many top/bottom stocks to return
    db: AsyncSession = Depends(get_db),                        # Inject DB session
) -> TopMoversOut:                                             # Return the TopMoversOut wrapper
    """
    Finds the latest trading date per ticker, then ranks by daily_return.
    Returns the top N gainers (highest return) and top N losers (lowest return).
    """

    # --- Step 1: Subquery to find the most recent date for each ticker ---
    latest_date_subq = (                                       # Build a subquery that returns max(date) per ticker
        select(                                                # SELECT ticker, MAX(date) AS max_date
            Price.ticker,                                      # Group by ticker
            func.max(Price.date).label("max_date")             # MAX(date) gives the most recent trading date for each ticker
        )
        .group_by(Price.ticker)                                # GROUP BY ticker — one row per stock
        .subquery()                                            # Wrap as a subquery so we can JOIN against it
    )

    # --- Step 2: Join prices + stocks against the subquery to get latest rows ---
    base_stmt = (
        select(Price, Stock)                                   # SELECT all columns from prices and stocks
        .join(Stock, Stock.ticker == Price.ticker)             # JOIN stocks ON stocks.ticker = prices.ticker
        .join(                                                 # JOIN the subquery ON ticker and date both match
            latest_date_subq,
            (latest_date_subq.c.ticker == Price.ticker) &     # Match the ticker
            (latest_date_subq.c.max_date == Price.date)        # AND match the most recent date for that ticker
        )
        .where(Price.daily_return.isnot(None))                 # Exclude rows where daily_return is NULL (the very first row per ticker)
    )

    # --- Gainers: sort by daily_return descending, take top N ---
    gainers_stmt = base_stmt.order_by(desc(Price.daily_return)).limit(n)  # ORDER BY daily_return DESC, return top N
    gainers_result = await db.execute(gainers_stmt)            # Execute async query
    gainers_rows = gainers_result.all()                        # Fetch all result rows (each row is a (Price, Stock) tuple)

    gainers = [                                                # Convert each (Price, Stock) result row into a TopMoverOut schema
        TopMoverOut(                                           # Build one TopMoverOut for each gainer row
            ticker=price.ticker,                               # Ticker from the prices row
            company_name=stock.company_name,                   # Company name from the joined stocks row
            sector=stock.sector,                               # Sector from the joined stocks row
            date=price.date,                                   # Latest trading date
            close=float(price.close) if price.close is not None else None,              # Latest closing price as float
            daily_return=float(price.daily_return) if price.daily_return is not None else None,  # Latest daily return as float
        )
        for price, stock in gainers_rows                       # Unpack the (Price, Stock) tuple from each result row
    ]

    # --- Losers: sort by daily_return ascending, take bottom N ---
    losers_stmt = base_stmt.order_by(Price.daily_return).limit(n)  # ORDER BY daily_return ASC — most negative first
    losers_result = await db.execute(losers_stmt)              # Execute async query
    losers_rows = losers_result.all()                          # Fetch all result rows

    losers = [                                                 # Convert each (Price, Stock) result row into a TopMoverOut
        TopMoverOut(
            ticker=price.ticker,
            company_name=stock.company_name,
            sector=stock.sector,
            date=price.date,
            close=float(price.close) if price.close is not None else None,
            daily_return=float(price.daily_return) if price.daily_return is not None else None,
        )
        for price, stock in losers_rows
    ]

    return TopMoversOut(gainers=gainers, losers=losers)        # Return both lists wrapped in the TopMoversOut response model


# =============================================================================
# GET /stocks/compare?tickers=AAPL,MSFT,GOOGL
# Returns a side-by-side snapshot of latest price and key indicators for each ticker.
# MUST be defined before /{ticker} to avoid 'compare' being matched as a ticker symbol.
# =============================================================================

@router.get(
    "/compare",                                                # Fixed path — must come before /{ticker}
    response_model=CompareOut,                                 # Response is a CompareOut with a results dict
    summary="Side-by-side comparison of multiple stocks",
    description=(
        "Returns the latest closing price and key indicators for each requested ticker. "
        "Pass a comma-separated list of tickers using the ?tickers= query parameter. "
        "Example: /stocks/compare?tickers=AAPL,MSFT,GOOGL"
    ),
)
async def compare_stocks(
    tickers: str = Query(                                      # ?tickers=AAPL,MSFT query parameter — comma-separated string
        ...,                                                   # Required — must be provided by the caller
        description="Comma-separated list of tickers to compare, e.g. AAPL,MSFT,GOOGL",
    ),
    db: AsyncSession = Depends(get_db),                        # Inject DB session
) -> CompareOut:                                               # Return the CompareOut wrapper
    """
    For each requested ticker, fetches the most recent price row and most recent
    indicator row, then assembles them into a side-by-side comparison object.
    """

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]  # Parse the comma-separated string into a clean list of uppercase tickers
    ticker_list = ticker_list[:10]                             # Cap at 10 tickers to prevent excessively large responses

    if not ticker_list:                                        # Guard: if the tickers param was empty or all whitespace
        raise HTTPException(                                   # Return HTTP 400 Bad Request with a descriptive error message
            status_code=400,
            detail="The 'tickers' parameter must contain at least one valid ticker symbol."
        )

    results = {}                                               # Dict to accumulate one CompareItemOut per ticker

    for ticker in ticker_list:                                 # Process each requested ticker one at a time
        # --- Fetch stock metadata ---
        stock = await db.get(Stock, ticker)                    # Look up the stocks table by primary key (ticker)
        if not stock:                                          # If this ticker is not in our database
            continue                                           # Skip it silently (don't crash — return results for the tickers we do have)

        # --- Fetch latest price row ---
        price_stmt = (
            select(Price)                                      # SELECT * FROM prices
            .where(Price.ticker == ticker)                     # WHERE ticker = requested ticker
            .order_by(desc(Price.date))                        # ORDER BY date DESC — most recent first
            .limit(1)                                          # LIMIT 1 — we only want the single latest row
        )
        price_result = await db.execute(price_stmt)            # Execute async query
        latest_price = price_result.scalars().first()          # Fetch the first (and only) result row, or None if no prices exist

        # --- Fetch latest indicator row ---
        ind_stmt = (
            select(Indicator)                                  # SELECT * FROM indicators
            .where(Indicator.ticker == ticker)                 # WHERE ticker = requested ticker
            .order_by(desc(Indicator.date))                    # ORDER BY date DESC — most recent first
            .limit(1)                                          # LIMIT 1 — only the latest indicator row
        )
        ind_result = await db.execute(ind_stmt)                # Execute async query
        latest_ind = ind_result.scalars().first()              # Fetch the first result, or None if no indicators yet

        results[ticker] = CompareItemOut(                      # Build the CompareItemOut snapshot for this ticker
            ticker=stock.ticker,                               # Ticker symbol
            company_name=stock.company_name,                   # Company name from stocks table
            sector=stock.sector,                               # Sector from stocks table

            # Latest price fields — all None if no price data exists yet
            latest_date=latest_price.date                if latest_price else None,      # Most recent trading date
            close=float(latest_price.close)              if latest_price and latest_price.close else None,         # Latest close price
            daily_return=float(latest_price.daily_return) if latest_price and latest_price.daily_return else None, # Latest daily return

            # Latest indicator fields — all None if no indicators exist yet
            sma_20=float(latest_ind.sma_20)       if latest_ind and latest_ind.sma_20 else None,       # Latest SMA-20
            sma_50=float(latest_ind.sma_50)       if latest_ind and latest_ind.sma_50 else None,       # Latest SMA-50
            rsi_14=float(latest_ind.rsi_14)       if latest_ind and latest_ind.rsi_14 else None,       # Latest RSI
            macd=float(latest_ind.macd)           if latest_ind and latest_ind.macd else None,         # Latest MACD line
            macd_signal=float(latest_ind.macd_signal) if latest_ind and latest_ind.macd_signal else None,  # Latest MACD signal
        )

    return CompareOut(                                         # Wrap the results dict in the CompareOut response model
        requested_tickers=ticker_list,                         # Echo back the list of tickers the client requested
        results=results,                                       # Dict of ticker → CompareItemOut objects
    )


# =============================================================================
# GET /stocks/{ticker}
# Returns metadata for a single stock.
# Dynamic path — must come AFTER all fixed paths (/top-movers, /compare).
# =============================================================================

@router.get(
    "/{ticker}",                                               # Dynamic path parameter — matches any string after /stocks/
    response_model=StockOut,                                   # Response is a single StockOut object
    summary="Get metadata for one stock",
    description="Returns company name, sector, exchange and market cap for the given ticker symbol.",
)
async def get_stock(
    ticker: str,                                               # FastAPI extracts the ticker from the URL path
    db: AsyncSession = Depends(get_db),                        # Inject DB session
) -> StockOut:                                                 # Return a single StockOut
    """Looks up one stock by ticker. Returns 404 if the ticker is not in our database."""

    ticker = ticker.upper()                                    # Normalise the ticker to uppercase so 'aapl' works the same as 'AAPL'
    stock = await db.get(Stock, ticker)                        # Primary-key lookup — fastest possible query; returns None if not found

    if not stock:                                              # If the ticker does not exist in the stocks table
        raise HTTPException(                                   # Return HTTP 404 Not Found with a clear error message
            status_code=404,
            detail=f"Ticker '{ticker}' not found. Make sure data_fetcher.py has been run."
        )

    return stock                                               # FastAPI serialises the Stock ORM object using StockOut


# =============================================================================
# GET /stocks/{ticker}/prices
# Returns paginated OHLCV price history for one stock.
# =============================================================================

@router.get(
    "/{ticker}/prices",                                        # Sub-path under the dynamic ticker parameter
    response_model=PriceListOut,                               # Response is a PriceListOut wrapper
    summary="OHLCV price history for one stock",
    description=(
        "Returns daily Open, High, Low, Close, Volume data for the given ticker. "
        "Ordered most-recent first. Use ?limit to control how many rows are returned."
    ),
)
async def get_prices(
    ticker: str,                                               # Ticker extracted from the URL path
    limit:      int  = Query(default=252, ge=1,  le=2000,  description="Number of rows to return (default 252 = ~1 year)"),  # ?limit — how many rows to return
    start_date: Optional[str] = Query(default=None, description="Filter rows on or after this date (YYYY-MM-DD)"),  # ?start_date — optional start filter
    end_date:   Optional[str] = Query(default=None, description="Filter rows on or before this date (YYYY-MM-DD)"),  # ?end_date — optional end filter
    db: AsyncSession = Depends(get_db),                        # Inject DB session
) -> PriceListOut:                                             # Return the PriceListOut wrapper
    """
    Returns price history for the given ticker, sorted most-recent first.
    Supports optional date range filtering via start_date and end_date query params.
    """

    ticker = ticker.upper()                                    # Normalise ticker to uppercase

    stock = await db.get(Stock, ticker)                        # Verify the ticker exists before querying prices
    if not stock:                                              # Return 404 if the ticker is not in our database
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found.")

    stmt = (
        select(Price)                                          # SELECT * FROM prices
        .where(Price.ticker == ticker)                         # WHERE ticker = requested ticker
        .order_by(desc(Price.date))                            # ORDER BY date DESC — most recent rows first
    )

    if start_date:                                             # Apply optional start date filter if provided by the caller
        from datetime import date as date_type                 # Import date type here to avoid top-level name collision
        stmt = stmt.where(Price.date >= start_date)            # WHERE date >= start_date

    if end_date:                                               # Apply optional end date filter if provided
        stmt = stmt.where(Price.date <= end_date)              # WHERE date <= end_date

    stmt = stmt.limit(limit)                                   # Apply the row limit after all filters are applied

    result = await db.execute(stmt)                            # Execute the async query
    prices = result.scalars().all()                            # Extract all Price ORM objects

    return PriceListOut(                                       # Wrap the results in the PriceListOut response model
        ticker=ticker,                                         # Echo back the ticker symbol
        count=len(prices),                                     # Total rows returned in this response
        prices=prices,                                         # The list of Price ORM objects (serialised by PriceOut)
    )


# =============================================================================
# GET /stocks/{ticker}/indicators
# Returns computed technical indicator history for one stock.
# =============================================================================

@router.get(
    "/{ticker}/indicators",                                    # Sub-path under the dynamic ticker parameter
    response_model=IndicatorListOut,                           # Response is an IndicatorListOut wrapper
    summary="Technical indicators history for one stock",
    description=(
        "Returns computed SMA, EMA, RSI, MACD, Bollinger Bands, Sharpe, Beta, "
        "and Drawdown values for the given ticker. Ordered most-recent first."
    ),
)
async def get_indicators(
    ticker: str,                                               # Ticker extracted from the URL path
    limit: int = Query(default=100, ge=1, le=2000, description="Number of rows to return (default 100)"),  # ?limit query param
    db: AsyncSession = Depends(get_db),                        # Inject DB session
) -> IndicatorListOut:                                         # Return the IndicatorListOut wrapper
    """
    Returns indicator history for the given ticker, sorted most-recent first.
    Sharpe, Beta, and Drawdown will be NULL for rows computed before 252 days of history existed.
    """

    ticker = ticker.upper()                                    # Normalise ticker to uppercase

    stock = await db.get(Stock, ticker)                        # Verify the ticker exists
    if not stock:                                              # Return 404 if not found
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found.")

    stmt = (
        select(Indicator)                                      # SELECT * FROM indicators
        .where(Indicator.ticker == ticker)                     # WHERE ticker = requested ticker
        .order_by(desc(Indicator.date))                        # ORDER BY date DESC — most recent first
        .limit(limit)                                          # Limit to requested number of rows
    )

    result = await db.execute(stmt)                            # Execute the async query
    indicators = result.scalars().all()                        # Extract all Indicator ORM objects

    return IndicatorListOut(                                   # Wrap results in the IndicatorListOut response model
        ticker=ticker,                                         # Echo the ticker symbol
        count=len(indicators),                                 # Number of rows returned
        indicators=indicators,                                 # The list of Indicator ORM objects (serialised by IndicatorOut)
    )
