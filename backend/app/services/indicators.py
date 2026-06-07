"""
indicators.py — Technical indicator computation pipeline.

Reads daily closing prices from the Supabase 'prices' table, computes all
12 technical indicators for every stock using pandas, then bulk-inserts the
results into the 'indicators' table.

Core indicators (sma_20, sma_50, rsi_14, MACD, Bollinger Bands) require at
least 50 days of history. Rows with insufficient history are skipped entirely.
Sharpe, Beta, and Drawdown require 252 days — stored as NULL until available.

Run directly from the backend/ directory:
    python -m app.services.indicators

Or call main() from the APScheduler job in scheduler.py.
"""

import sys                                              # Used to insert the backend/ directory into the Python path
import os                                               # Used to build the absolute path for the sys.path insert

# ---------------------------------------------------------------------------
# PATH FIX — same pattern as data_fetcher.py
# Ensures 'from app.core.config import settings' works when run directly.
# ---------------------------------------------------------------------------
sys.path.insert(                                        # Add the backend/ directory to Python's module search path
    0,                                                  # Position 0 = highest priority, checked first
    os.path.abspath(                                    # Convert relative path to absolute so it works from any working directory
        os.path.join(                                   # Join path components together
            os.path.dirname(__file__),                  # This file lives at backend/app/services/ — start here
            "..", "..", ".."                            # Go up 3 levels to reach backend/
        )
    )
)

import psycopg2                                         # Synchronous PostgreSQL driver — used for reading prices and writing indicators
from psycopg2.extras import execute_values              # Bulk-insert helper — sends many rows in a single SQL call
import pandas as pd                                     # pandas — all indicator maths is done on DataFrames and Series
import numpy as np                                      # numpy — used for sqrt(252) in Sharpe ratio and NaN checks
from app.core.config import settings                    # Shared settings object — provides SUPABASE_DB_URL from the .env file


# ---------------------------------------------------------------------------
# TICKERS TO PROCESS
# All 20 tracked stocks plus SPY (the benchmark used for Beta calculation).
# We compute indicators for every ticker including SPY — SPY's own Beta is 1.0.
# ---------------------------------------------------------------------------

TICKERS = [                                             # Full list of tickers we will process in order
    "AAPL", "GOOGL", "MSFT", "NVDA", "META",           # Technology sector
    "JPM", "GS", "BAC", "V", "MA",                     # Financial services sector
    "JNJ", "UNH", "PFE",                               # Healthcare sector
    "XOM", "CVX",                                       # Energy sector
    "AMZN", "TSLA", "WMT",                             # Consumer sector
    "GME",                                              # Volatile / anomaly detection pick
    "SPY",                                              # S&P 500 ETF benchmark — used as reference for Beta
]

RISK_FREE_RATE_DAILY = 0.05 / 252                       # Daily risk-free rate: annualised 5% US Treasury rate divided by 252 trading days


# ---------------------------------------------------------------------------
# HELPER: build psycopg2-compatible connection string
# ---------------------------------------------------------------------------

def build_db_url() -> str:                              # Returns a plain postgresql:// URL that psycopg2 can connect to
    """Strips the '+asyncpg' SQLAlchemy driver prefix from SUPABASE_DB_URL."""
    raw_url = settings.SUPABASE_DB_URL                  # Read the full URL from settings (starts with postgresql+asyncpg://)
    return raw_url.replace(                             # Remove the async driver qualifier that psycopg2 cannot understand
        "postgresql+asyncpg://",                        # The SQLAlchemy async format prefix
        "postgresql://"                                 # Replace with the standard psycopg2 format
    )


# ---------------------------------------------------------------------------
# STEP 1: Fetch all price data from the database
# Loads closing prices and daily returns for all tickers in one query.
# Returns a dict mapping each ticker to its sorted DataFrame.
# ---------------------------------------------------------------------------

def fetch_all_prices(cursor) -> dict:                   # Returns a dict: {ticker (str) -> pd.DataFrame with date index}
    """
    Fetches date, close, and daily_return for every ticker from the prices table.
    Returns a dictionary of DataFrames, one per ticker, sorted by date ascending.
    """
    cursor.execute("""
        SELECT ticker, date, close, daily_return
        FROM prices
        ORDER BY ticker, date ASC
    """)                                                # Fetch all rows sorted by ticker then date so grouping is straightforward

    rows = cursor.fetchall()                            # Retrieve all rows into memory (max ~21 tickers × 500 days = ~10,500 rows — manageable)

    grouped = {}                                        # Temporary dict to collect rows per ticker before converting to DataFrames
    for ticker, date, close, daily_return in rows:      # Unpack each row into its four columns
        if ticker not in grouped:                       # First time we see this ticker — create an empty list for it
            grouped[ticker] = []                        # Initialise the list that will hold this ticker's row dicts
        grouped[ticker].append({                        # Append a dict for this trading day to the ticker's list
            "date":         pd.Timestamp(date),         # Convert the Python date to pandas Timestamp for consistent index handling
            "close":        float(close) if close is not None else np.nan,          # Convert Decimal to float; use NaN if the DB value is NULL
            "daily_return": float(daily_return) if daily_return is not None else np.nan,  # Same conversion for daily_return
        })

    result = {}                                         # Final dict that maps ticker -> DataFrame (what we return to the caller)
    for ticker, records in grouped.items():             # Iterate over each ticker's list of row dicts
        df = pd.DataFrame(records)                      # Convert the list of dicts into a pandas DataFrame
        df = df.set_index("date")                       # Use the date column as the row index so rolling/ewm operations work correctly
        df = df.sort_index()                            # Sort by date ascending — essential for all rolling window calculations
        result[ticker] = df                             # Store the finished DataFrame in the result dict

    return result                                       # Return the complete dict of DataFrames


# ---------------------------------------------------------------------------
# STEP 2: Compute RSI-14
# RSI uses Wilder's smoothing (EWM with alpha=1/14).
# Returns a Series of RSI values aligned to the same index as 'close'.
# ---------------------------------------------------------------------------

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:  # close: Series of closing prices; returns Series of RSI values (0-100)
    """
    Computes the Relative Strength Index using Wilder's exponential smoothing.
    Values > 70 indicate the stock may be overbought; values < 30 indicate oversold.
    """
    delta = close.diff()                                # Day-over-day price change: today's close minus yesterday's close
    gain  = delta.clip(lower=0)                         # Keep only positive changes (gains); replace negative changes with 0
    loss  = (-delta).clip(lower=0)                      # Flip sign to make losses positive; replace gains (now negative) with 0

    avg_gain = gain.ewm(                                # Apply Wilder's exponential smoothing to the gain Series
        alpha=1 / period,                               # alpha = 1/14 — equivalent to Wilder's smoothing factor
        adjust=False,                                   # Use recursive formula (not the correction factor), matching the standard RSI definition
        min_periods=period                              # Return NaN until we have at least 14 periods of data
    ).mean()                                            # Compute the exponentially weighted mean of gains

    avg_loss = loss.ewm(                                # Apply the same smoothing to the loss Series
        alpha=1 / period,                               # Same alpha as gain (1/14)
        adjust=False,                                   # Same recursive formula
        min_periods=period                              # Same minimum periods requirement
    ).mean()                                            # Compute the exponentially weighted mean of losses

    rs  = avg_gain / avg_loss                           # Relative Strength: ratio of average gain to average loss
    rsi = 100 - (100 / (1 + rs))                        # Convert RS to the 0-100 RSI scale using the standard formula
    return rsi                                          # Return the Series of RSI values (NaN for first 14 rows)


# ---------------------------------------------------------------------------
# STEP 3: Compute rolling Sharpe Ratio (252-day window)
# Sharpe = (mean_daily_return - daily_risk_free_rate) / std_daily_return * sqrt(252)
# Annualised by multiplying by sqrt(252) trading days.
# ---------------------------------------------------------------------------

def compute_sharpe(daily_return: pd.Series, window: int = 252) -> pd.Series:  # Returns Series of annualised Sharpe ratios
    """
    Computes the rolling 252-day Sharpe ratio.
    A higher Sharpe ratio means better risk-adjusted return.
    Returns NaN for dates with fewer than 252 days of history.
    """
    rolling_mean = daily_return.rolling(                # Rolling window of 252 trading days (~1 year)
        window=window,                                  # Window size: 252 days
        min_periods=window                              # Require a full 252 days — no partial windows
    ).mean()                                            # Mean daily return over the window

    rolling_std = daily_return.rolling(                 # Same rolling window for standard deviation
        window=window,                                  # Same 252-day window
        min_periods=window                              # Same full-window requirement
    ).std()                                             # Standard deviation of daily returns (volatility proxy)

    sharpe = (                                          # Annualised Sharpe ratio formula
        (rolling_mean - RISK_FREE_RATE_DAILY)           # Excess return above the risk-free rate (per day)
        / rolling_std                                   # Divided by daily volatility
        * np.sqrt(window)                               # Annualise: multiply by sqrt(252) to convert from daily to annual scale
    )
    return sharpe                                       # Return Series of Sharpe ratios (NaN for first 252 rows)


# ---------------------------------------------------------------------------
# STEP 4: Compute rolling Beta vs SPY (252-day window)
# Beta = covariance(stock_returns, spy_returns) / variance(spy_returns)
# Measures how much the stock moves relative to the overall market.
# ---------------------------------------------------------------------------

def compute_beta(
    stock_returns: pd.Series,                           # Daily returns for the stock being analysed
    spy_returns:   pd.Series,                           # Daily returns for SPY (the S&P 500 benchmark)
    window: int = 252                                   # Rolling window size in trading days
) -> pd.Series:                                         # Returns Series of rolling beta values
    """
    Computes rolling 252-day Beta of the stock vs SPY.
    Beta > 1 means the stock is more volatile than the market.
    Beta < 1 means the stock is less volatile than the market.
    Returns NaN for dates with fewer than 252 days of history.
    """
    aligned = pd.concat(                                # Combine stock and SPY returns into one DataFrame aligned by date
        [stock_returns, spy_returns],                   # Two Series to align
        axis=1,                                         # Stack them as columns (not rows)
        keys=["stock", "spy"]                           # Name the columns clearly for readability
    )

    covariance = aligned["stock"].rolling(              # Rolling covariance between stock returns and SPY returns
        window=window,                                  # 252-day rolling window
        min_periods=window                              # Require a full 252 days before producing a value
    ).cov(aligned["spy"])                               # pandas rolling().cov() computes the rolling covariance against a second Series

    variance = aligned["spy"].rolling(                  # Rolling variance of SPY returns (the denominator in the Beta formula)
        window=window,                                  # Same 252-day window
        min_periods=window                              # Same full-window requirement
    ).var()                                             # pandas rolling().var() computes the rolling variance

    beta = covariance / variance                        # Beta = rolling covariance / rolling variance (standard definition)
    return beta                                         # Return Series of beta values (NaN for first 252 rows)


# ---------------------------------------------------------------------------
# STEP 5: Compute rolling maximum drawdown (252-day window)
# Drawdown = (close - rolling_252_day_peak) / rolling_252_day_peak
# A negative value, e.g. -0.15 means the stock is 15% below its 252-day high.
# ---------------------------------------------------------------------------

def compute_drawdown(close: pd.Series, window: int = 252) -> pd.Series:  # Returns Series of drawdown values (negative floats)
    """
    Computes the current drawdown from the 252-day rolling maximum closing price.
    Negative values indicate how far the current price has fallen from the recent peak.
    Returns NaN for dates with fewer than 252 days of history.
    """
    rolling_peak = close.rolling(                       # Rolling maximum: the highest closing price in the past 252 days
        window=window,                                  # 252-day lookback window
        min_periods=window                              # Require a full 252 days before computing (no partial peaks)
    ).max()                                             # pandas rolling().max() returns the highest value in each window

    drawdown = (close - rolling_peak) / rolling_peak    # Current drawdown: how far is today's price below the 252-day peak?
    return drawdown                                     # Returns a negative Series (e.g. -0.123456 means 12.35% below the peak)


# ---------------------------------------------------------------------------
# STEP 6: Compute all indicators for one ticker
# Combines all the individual functions above into one DataFrame of results.
# Returns only rows where all core indicators (needing ≤50 days) are non-NaN.
# Sharpe, Beta, and Drawdown are allowed to be NaN (stored as NULL in DB).
# ---------------------------------------------------------------------------

def compute_indicators(
    df:          pd.DataFrame,                          # Price DataFrame for this ticker (date index, close + daily_return columns)
    spy_returns: pd.Series,                             # SPY daily returns Series used only for Beta calculation
    ticker:      str                                    # Ticker symbol, used in status messages
) -> pd.DataFrame:                                      # Returns DataFrame of computed indicator values, filtered to valid rows only
    """
    Computes all 12 indicators for a single stock's price history.
    Only returns rows where the core indicators (SMA-50 and below) are not NaN.
    Sharpe, Beta, and Drawdown are nullable — they appear as NULL in the database
    until 252 days of history are available.
    """
    close         = df["close"]                         # Extract the closing price Series — all indicator calculations start from this
    daily_return  = df["daily_return"]                  # Extract the pre-computed daily return Series from the prices table

    # --- Moving averages ---
    sma_20 = close.rolling(window=20, min_periods=20).mean()   # Simple Moving Average over 20 days: arithmetic mean of the last 20 closing prices
    sma_50 = close.rolling(window=50, min_periods=50).mean()   # Simple Moving Average over 50 days: longer-term trend signal, needs 50 days of history
    ema_20 = close.ewm(span=20, adjust=False, min_periods=20).mean()  # Exponential Moving Average: span=20 means α=2/(20+1), more weight to recent prices

    # --- RSI ---
    rsi_14 = compute_rsi(close, period=14)              # Relative Strength Index: calls our helper function defined above

    # --- MACD ---
    ema_12      = close.ewm(span=12, adjust=False, min_periods=12).mean()  # EMA-12: short-term exponential average, reacts quickly to price changes
    ema_26      = close.ewm(span=26, adjust=False, min_periods=26).mean()  # EMA-26: long-term exponential average, reacts more slowly
    macd        = ema_12 - ema_26                       # MACD line: difference between EMA-12 and EMA-26 — positive means upward momentum
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()  # Signal line: 9-day EMA of the MACD line — crossovers signal buy/sell

    # --- Bollinger Bands ---
    std_20           = close.rolling(window=20, min_periods=20).std()    # 20-day rolling standard deviation of closing prices (measures volatility)
    bollinger_mid    = sma_20                           # Bollinger midline is exactly the SMA-20 (no new calculation needed)
    bollinger_upper  = sma_20 + (2 * std_20)            # Upper band: midline plus 2 standard deviations — price above here may reverse
    bollinger_lower  = sma_20 - (2 * std_20)            # Lower band: midline minus 2 standard deviations — price below here may reverse

    # --- Sharpe Ratio (rolling 252-day, nullable) ---
    sharpe_ratio = compute_sharpe(daily_return, window=252)  # Annualised Sharpe ratio — NaN until 252 days of daily_return data exist

    # --- Beta vs SPY (rolling 252-day, nullable) ---
    beta = compute_beta(                                # Rolling Beta vs SPY — NaN until 252 days of aligned returns exist
        stock_returns=daily_return,                     # This stock's daily return Series (indexed by date)
        spy_returns=spy_returns,                        # SPY's daily return Series (indexed by date) passed in from the caller
        window=252                                      # 252-day rolling window (~1 trading year)
    )

    # --- Maximum Drawdown (rolling 252-day, nullable) ---
    drawdown = compute_drawdown(close, window=252)      # Current drawdown from the 252-day rolling peak — NaN until 252 days exist

    # --- Assemble result DataFrame ---
    result = pd.DataFrame({                             # Combine all computed Series into one DataFrame aligned by date index
        "sma_20":         sma_20,                       # Column matching the 'sma_20' column in the indicators table
        "sma_50":         sma_50,                       # Column matching the 'sma_50' column
        "ema_20":         ema_20,                       # Column matching the 'ema_20' column
        "rsi_14":         rsi_14,                       # Column matching the 'rsi_14' column
        "macd":           macd,                         # Column matching the 'macd' column
        "macd_signal":    macd_signal,                  # Column matching the 'macd_signal' column
        "bollinger_upper": bollinger_upper,              # Column matching the 'bollinger_upper' column
        "bollinger_lower": bollinger_lower,              # Column matching the 'bollinger_lower' column
        "bollinger_mid":   bollinger_mid,               # Column matching the 'bollinger_mid' column
        "sharpe_ratio":   sharpe_ratio,                 # Column matching the 'sharpe_ratio' column (nullable)
        "beta":           beta,                         # Column matching the 'beta' column (nullable)
        "drawdown":       drawdown,                     # Column matching the 'drawdown' column (nullable)
    })

    # --- Filter: keep only rows where ALL core indicators are non-NaN ---
    # Core indicators = those needing ≤50 days of history.
    # We use sma_50 as the filter because it has the longest requirement (50 days).
    # Once sma_50 is available, all other core indicators are also available.
    core_columns = [                                    # Define which columns must be non-NaN for a row to be worth inserting
        "sma_20", "sma_50", "ema_20", "rsi_14",        # Moving averages and momentum — all require ≤50 days
        "macd", "macd_signal",                          # MACD lines — require 26 days (shorter than SMA-50)
        "bollinger_upper", "bollinger_lower", "bollinger_mid",  # Bollinger Bands — require 20 days
    ]
    valid_mask = result[core_columns].notna().all(axis=1)  # Boolean mask: True for rows where ALL core indicator columns are non-NaN
    result = result[valid_mask]                         # Drop rows where any core indicator is NaN (insufficient history)

    return result                                       # Return the filtered DataFrame — ready to be inserted into the indicators table


# ---------------------------------------------------------------------------
# STEP 7: Insert computed indicators into the database
# Builds a list of tuples and bulk-inserts using execute_values.
# ON CONFLICT DO NOTHING skips rows already present in the table.
# ---------------------------------------------------------------------------

def insert_indicators(cursor, ticker: str, df: pd.DataFrame) -> int:  # Returns count of rows attempted
    """
    Bulk-inserts all computed indicator rows for one ticker into the indicators table.
    Skips any (ticker, date) pair that already exists — safe to re-run.
    Returns the number of rows attempted (includes silently skipped duplicates).
    """
    rows = []                                           # Collect all row tuples before executing the bulk insert

    for date_index, row in df.iterrows():               # Iterate over every valid date in the computed indicator DataFrame
        trade_date = date_index.date()                  # Convert the pandas Timestamp index to a Python date object

        def safe(value):                                # Inner helper: converts pandas/numpy floats to Python floats or None
            """Returns None if the value is NaN, otherwise returns a rounded Python float."""
            if pd.isna(value):                          # pd.isna() catches both numpy NaN and pandas NA
                return None                             # Store as NULL in the database (for sharpe, beta, drawdown before 252 days)
            return round(float(value), 6)               # Round to 6 decimal places to stay within NUMERIC column precision

        rows.append((                                   # Append a tuple with all 14 values in the exact INSERT column order
            ticker,                                     # indicators.ticker — foreign key to stocks.ticker
            trade_date,                                 # indicators.date — the trading date
            safe(row["sma_20"]),                        # indicators.sma_20
            safe(row["sma_50"]),                        # indicators.sma_50
            safe(row["ema_20"]),                        # indicators.ema_20
            safe(row["rsi_14"]),                        # indicators.rsi_14
            safe(row["macd"]),                          # indicators.macd
            safe(row["macd_signal"]),                   # indicators.macd_signal
            safe(row["bollinger_upper"]),               # indicators.bollinger_upper
            safe(row["bollinger_lower"]),               # indicators.bollinger_lower
            safe(row["bollinger_mid"]),                 # indicators.bollinger_mid
            safe(row["sharpe_ratio"]),                  # indicators.sharpe_ratio (NULL until 252 days of history)
            safe(row["beta"]),                          # indicators.beta (NULL until 252 days of aligned SPY data)
            safe(row["drawdown"]),                      # indicators.drawdown (NULL until 252 days of history)
        ))

    if not rows:                                        # Guard: if no valid rows were built (e.g. ticker has <50 days of data), skip
        return 0                                        # Return 0 so the caller knows nothing was inserted

    insert_sql = """
        INSERT INTO indicators (
            ticker, date,
            sma_20, sma_50, ema_20, rsi_14,
            macd, macd_signal,
            bollinger_upper, bollinger_lower, bollinger_mid,
            sharpe_ratio, beta, drawdown
        )
        VALUES %s
        ON CONFLICT (ticker, date) DO NOTHING
    """                                                 # SQL: insert all rows, silently skip duplicates using the UNIQUE(ticker, date) constraint

    execute_values(cursor, insert_sql, rows)            # Bulk-insert all rows in a single database round-trip (much faster than row-by-row)
    return len(rows)                                    # Return the count of rows attempted (inserted + skipped duplicates)


# ---------------------------------------------------------------------------
# MAIN FUNCTION
# Orchestrates the full indicator computation pipeline for all tickers.
# ---------------------------------------------------------------------------

def main() -> None:                                     # Entry point — callable from the scheduler or run directly
    """
    Full indicator pipeline:
    1. Connect to Supabase PostgreSQL
    2. Load all price data into memory
    3. Extract SPY returns for use as the Beta benchmark
    4. For each ticker: compute indicators → insert into DB → commit
    5. Close the connection
    """
    print("=" * 60)                                     # Visual separator for easy log reading
    print("Stock Market Analytics — Indicator Calculator")  # Script header
    print("=" * 60)                                     # Closing separator

    db_url = build_db_url()                             # Convert the async URL to a psycopg2-compatible URL

    print("Connecting to Supabase PostgreSQL...")        # Inform the user a connection is being opened
    conn   = psycopg2.connect(db_url)                   # Open a synchronous connection to Supabase
    conn.autocommit = False                             # Manage transactions manually — we commit per-ticker for partial-progress safety
    cursor = conn.cursor()                              # Create a cursor for executing SQL statements

    try:
        print("Loading all price data from database...")    # Status: fetching prices before any computation begins
        all_prices = fetch_all_prices(cursor)               # Load all tickers' price history into memory as a dict of DataFrames

        if "SPY" not in all_prices:                         # SPY must be in the prices table — it is required for Beta calculation
            print("ERROR: SPY price data not found. Run data_fetcher.py first.")  # Tell the user what to do to fix this
            return                                          # Exit early — we cannot compute Beta without SPY data

        spy_returns = all_prices["SPY"]["daily_return"]     # Extract SPY's daily return Series — this is the Beta benchmark for all stocks
        print(f"  Loaded price data for {len(all_prices)} tickers")  # Confirm how many tickers were loaded

        print()                                             # Blank line before per-ticker progress output
        total_rows = 0                                      # Running total of indicator rows inserted across all tickers

        for i, ticker in enumerate(TICKERS, start=1):      # Iterate over every ticker we want to compute indicators for
            print(f"[{i}/{len(TICKERS)}] Computing indicators for {ticker}...")  # Progress line: e.g. "[1/21] Computing indicators for AAPL..."

            if ticker not in all_prices:                    # Check if this ticker actually has price data loaded
                print(f"  Skipped {ticker} — no price data in database")  # Warn: price data missing, likely data_fetcher.py hasn't been run
                continue                                    # Skip to the next ticker

            price_df = all_prices[ticker]                   # Retrieve this ticker's DataFrame (date index, close, daily_return columns)

            if len(price_df) < 50:                          # Need at least 50 rows for SMA-50 — skip if insufficient history
                print(f"  Skipped {ticker} — only {len(price_df)} rows (need at least 50)")  # Inform the user why this ticker was skipped
                continue                                    # Skip to the next ticker

            indicator_df = compute_indicators(             # Compute all 12 indicators for this ticker
                df=price_df,                               # This ticker's price DataFrame
                spy_returns=spy_returns,                   # SPY returns for Beta calculation
                ticker=ticker                              # Ticker string for log messages inside the function
            )

            if indicator_df.empty:                         # If all rows were filtered out (e.g. all NaN after rolling windows)
                print(f"  Skipped {ticker} — no valid indicator rows after filtering")  # Shouldn't happen often, but guard against it
                continue                                   # Skip to the next ticker

            rows_inserted = insert_indicators(cursor, ticker, indicator_df)  # Bulk-insert this ticker's indicator rows into the DB
            conn.commit()                                  # Commit immediately after each ticker — partial progress is preserved if a later ticker fails

            total_rows += rows_inserted                    # Add to the running grand total
            print(f"  Inserted {rows_inserted} rows for {ticker}")  # Report success with row count

        print()                                            # Blank line before the final summary
        print("=" * 60)                                    # Visual separator
        print(f"Done! Total indicator rows inserted: {total_rows}")  # Grand total across all tickers
        print("=" * 60)                                    # Closing separator

    except Exception as e:
        conn.rollback()                                    # Roll back any uncommitted work if an unexpected error occurred
        print(f"\nERROR: {e}")                             # Print the error message for debugging
        raise                                              # Re-raise so the calling scheduler knows the job failed

    finally:
        cursor.close()                                     # Always close the cursor to release the server-side resource
        conn.close()                                       # Always close the connection even if an error occurred
        print("Database connection closed.")               # Confirm clean closure


# ---------------------------------------------------------------------------
# SCRIPT ENTRY POINT
# Only runs when executed directly — not when imported by scheduler.py.
# ---------------------------------------------------------------------------

if __name__ == "__main__":                                # Standard Python guard: run main() only when this file is executed directly
    main()                                                # Start the full indicator computation and insertion pipeline
