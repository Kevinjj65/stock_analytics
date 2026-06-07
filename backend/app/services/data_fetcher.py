"""
data_fetcher.py — One-time and scheduled OHLCV data loader.

Fetches 2 years of daily price history for all 20 tracked stocks plus SPY
from yfinance and inserts the data into the Supabase 'prices' table.
Also seeds the 'stocks' table with metadata if rows don't exist yet.

Run directly from the backend/ directory:
    python -m app.services.data_fetcher

Or import and call main() from the APScheduler job in scheduler.py.
"""

import sys                                              # Used to manipulate the Python module search path
import os                                               # Used to build absolute file paths for the sys.path insert

# ---------------------------------------------------------------------------
# PATH FIX
# When this script is run directly (not as part of the FastAPI app),
# Python doesn't know where 'app' is unless we tell it.
# We insert the backend/ directory into the search path so 'from app.core...' works.
# ---------------------------------------------------------------------------
sys.path.insert(                                        # Add a directory to the front of Python's module search list
    0,                                                  # Position 0 = highest priority (checked first)
    os.path.abspath(                                    # Convert the relative path to an absolute path
        os.path.join(                                   # Build the path by joining components
            os.path.dirname(__file__),                  # Directory of this file: backend/app/services/
            "..", "..", ".."                            # Go up 3 levels to reach the backend/ directory
        )
    )
)

import yfinance as yf                                   # yfinance library — fetches stock data from Yahoo Finance for free
import psycopg2                                         # psycopg2 — synchronous PostgreSQL driver used here for simple bulk inserts
from psycopg2.extras import execute_values              # execute_values — bulk-inserts many rows in one efficient SQL call
import pandas as pd                                     # pandas — used to work with the DataFrame that yfinance returns
from datetime import datetime, timedelta                # datetime for date calculations, timedelta for computing the 2-year window
from app.core.config import settings                    # Import our settings object to read SUPABASE_DB_URL from .env


# ---------------------------------------------------------------------------
# STOCK METADATA
# Hardcoded metadata for all 20 tracked stocks plus SPY (benchmark).
# Format: ticker -> (company_name, sector, exchange)
# This seeds the 'stocks' table so foreign keys in 'prices' resolve correctly.
# ---------------------------------------------------------------------------

STOCK_METADATA = {                                                                        # Dictionary mapping each ticker to its static metadata
    "AAPL":  ("Apple Inc.",                       "Technology",          "NASDAQ"),       # Apple — largest company by market cap, flagship tech stock
    "GOOGL": ("Alphabet Inc.",                    "Technology",          "NASDAQ"),       # Alphabet / Google — search, cloud, and advertising
    "MSFT":  ("Microsoft Corporation",            "Technology",          "NASDAQ"),       # Microsoft — enterprise software, Azure cloud, Office 365
    "NVDA":  ("NVIDIA Corporation",               "Technology",          "NASDAQ"),       # NVIDIA — GPU chips, AI hardware, data centre growth
    "META":  ("Meta Platforms Inc.",              "Technology",          "NASDAQ"),       # Meta / Facebook — social media and AR/VR investments
    "JPM":   ("JPMorgan Chase & Co.",             "Financial Services",  "NYSE"),         # JPMorgan — largest US bank by assets
    "GS":    ("Goldman Sachs Group Inc.",         "Financial Services",  "NYSE"),         # Goldman Sachs — investment banking and trading
    "BAC":   ("Bank of America Corporation",      "Financial Services",  "NYSE"),         # Bank of America — retail and commercial banking
    "V":     ("Visa Inc.",                        "Financial Services",  "NYSE"),         # Visa — global payments network
    "MA":    ("Mastercard Incorporated",          "Financial Services",  "NYSE"),         # Mastercard — global payments network (Visa competitor)
    "JNJ":   ("Johnson & Johnson",               "Healthcare",          "NYSE"),         # J&J — pharmaceuticals, medical devices, consumer health
    "UNH":   ("UnitedHealth Group Incorporated", "Healthcare",          "NYSE"),         # UnitedHealth — largest US health insurer
    "PFE":   ("Pfizer Inc.",                      "Healthcare",          "NYSE"),         # Pfizer — pharmaceuticals including COVID vaccines
    "XOM":   ("Exxon Mobil Corporation",          "Energy",             "NYSE"),         # ExxonMobil — largest US oil and gas company
    "CVX":   ("Chevron Corporation",              "Energy",             "NYSE"),         # Chevron — integrated energy company
    "AMZN":  ("Amazon.com Inc.",                  "Consumer Cyclical",   "NASDAQ"),       # Amazon — e-commerce, AWS cloud, and Prime ecosystem
    "TSLA":  ("Tesla Inc.",                       "Consumer Cyclical",   "NASDAQ"),       # Tesla — electric vehicles and energy storage
    "WMT":   ("Walmart Inc.",                     "Consumer Defensive",  "NYSE"),         # Walmart — largest US retailer by revenue
    "GME":   ("GameStop Corp.",                   "Consumer Cyclical",   "NYSE"),         # GameStop — included for meme stock / anomaly detection interest
    "SPY":   ("SPDR S&P 500 ETF Trust",           "ETF / Benchmark",    "NYSE Arca"),    # SPY — S&P 500 ETF used as the market benchmark for Beta calculation
}

TICKERS = list(STOCK_METADATA.keys())                  # Ordered list of all ticker symbols we will fetch data for — 20 stocks + SPY


# ---------------------------------------------------------------------------
# HELPER: build psycopg2 connection string
# Our .env stores the URL in SQLAlchemy's async format: postgresql+asyncpg://...
# psycopg2 needs the plain format:                     postgresql://...
# We strip the '+asyncpg' driver suffix before connecting.
# ---------------------------------------------------------------------------

def build_db_url() -> str:                                          # Returns a psycopg2-compatible connection string
    """Converts the SQLAlchemy async DB URL to a plain psycopg2-compatible URL."""
    raw_url = settings.SUPABASE_DB_URL                              # Read the full URL from settings (e.g. postgresql+asyncpg://user:pass@host/db)
    psycopg2_url = raw_url.replace(                                 # Remove the '+asyncpg' driver qualifier that SQLAlchemy needs but psycopg2 rejects
        "postgresql+asyncpg://",                                    # The SQLAlchemy async prefix to search for
        "postgresql://"                                             # Replace with the standard psycopg2 prefix
    )
    return psycopg2_url                                             # Return the cleaned URL ready for psycopg2.connect()


# ---------------------------------------------------------------------------
# STEP 1: Seed the stocks table
# Insert each ticker's metadata into the 'stocks' table.
# ON CONFLICT DO NOTHING means if a ticker already exists, we skip it safely.
# ---------------------------------------------------------------------------

def seed_stocks_table(cursor) -> None:                              # cursor is an active psycopg2 cursor connected to Supabase
    """
    Inserts all tickers into the stocks table if they don't already exist.
    Uses ON CONFLICT DO NOTHING to skip tickers that were previously inserted.
    """
    print("Seeding stocks table...")                                # Inform the user that stock metadata seeding is starting

    rows = []                                                       # Empty list to collect the rows we will insert
    for ticker, (company_name, sector, exchange) in STOCK_METADATA.items():  # Iterate through every ticker and its metadata
        rows.append((                                               # Append a tuple representing one row in the stocks table
            ticker,                                                 # stocks.ticker — the primary key
            company_name,                                           # stocks.company_name — full company name
            sector,                                                 # stocks.sector — business sector category
            None,                                                   # stocks.market_cap — left NULL here; yfinance data changes daily
            exchange,                                               # stocks.exchange — stock exchange name
        ))

    insert_sql = """
        INSERT INTO stocks (ticker, company_name, sector, market_cap, exchange)
        VALUES %s
        ON CONFLICT (ticker) DO NOTHING
    """                                                             # SQL template: insert rows, skip any that already have the same ticker primary key

    execute_values(cursor, insert_sql, rows)                        # Bulk-insert all rows in one efficient database round-trip
    print(f"  Seeded {len(rows)} stock records (existing ones skipped)")  # Confirm how many rows were attempted


# ---------------------------------------------------------------------------
# STEP 2: Fetch OHLCV data from yfinance
# Downloads 2 years of daily price history for a single ticker.
# auto_adjust=True adjusts prices for stock splits and dividends automatically.
# ---------------------------------------------------------------------------

def fetch_ohlcv(ticker: str) -> pd.DataFrame:                       # Returns a pandas DataFrame with OHLCV columns, or empty DataFrame on failure
    """
    Downloads 2 years of daily OHLCV data for the given ticker using yfinance.
    Returns an empty DataFrame if the download fails or returns no data.
    """
    end_date   = datetime.today()                                   # Today's date — the most recent trading day we can fetch
    start_date = end_date - timedelta(days=2 * 365)                 # 2 years ago — approx 730 days of history requested

    try:
        data = yf.download(                                         # Call yfinance to download price history from Yahoo Finance
            ticker,                                                 # The stock ticker symbol to download (e.g. 'AAPL')
            start=start_date.strftime("%Y-%m-%d"),                  # Start date formatted as YYYY-MM-DD string
            end=end_date.strftime("%Y-%m-%d"),                      # End date formatted as YYYY-MM-DD string
            auto_adjust=True,                                       # Automatically adjust prices for splits and dividends (no raw prices)
            progress=False,                                         # Suppress yfinance's own download progress bar (we print our own)
            multi_level_index=False,                                # Return a flat single-level column DataFrame (not a MultiIndex)
        )
    except Exception as e:
        print(f"  ERROR downloading {ticker}: {e}")                 # Print the error so we know which ticker failed and why
        return pd.DataFrame()                                       # Return an empty DataFrame so the caller can detect the failure

    if data.empty:                                                  # Check if yfinance returned no rows (can happen for delisted tickers)
        print(f"  WARNING: No data returned for {ticker}")          # Warn the user so they know this ticker was skipped
        return pd.DataFrame()                                       # Return empty DataFrame to skip this ticker in the insert step

    return data                                                     # Return the populated DataFrame with Date index and OHLCV columns


# ---------------------------------------------------------------------------
# STEP 3: Calculate daily_return
# daily_return = (today's close - yesterday's close) / yesterday's close
# The first row always has NaN because there is no previous day to compare to.
# ---------------------------------------------------------------------------

def calculate_daily_return(df: pd.DataFrame) -> pd.Series:         # Returns a pandas Series of float daily return values
    """
    Computes the percentage daily return for each row in the DataFrame.
    Result is NaN for the first row (no previous close to compare against).
    """
    close_prices = df["Close"]                                      # Extract the 'Close' column as a Series of closing prices
    daily_return = close_prices.pct_change()                        # pct_change() computes (current - previous) / previous for each row
    return daily_return                                             # Return the Series — same index as the input DataFrame


# ---------------------------------------------------------------------------
# STEP 4: Insert price rows into the database
# Builds a list of tuples and bulk-inserts them into the prices table.
# ON CONFLICT DO NOTHING prevents errors if we re-run the script.
# ---------------------------------------------------------------------------

def insert_prices(cursor, ticker: str, df: pd.DataFrame) -> int:   # Returns the number of rows inserted
    """
    Inserts all rows from the yfinance DataFrame into the prices table.
    Skips any rows where (ticker, date) already exists in the table.
    Returns the count of rows that were successfully inserted.
    """
    daily_returns = calculate_daily_return(df)                      # Calculate daily returns for all rows in this ticker's history

    rows = []                                                       # Empty list to collect (ticker, date, open, high, low, close, volume, daily_return) tuples
    for date_index, row in df.iterrows():                           # Iterate over every trading day in the DataFrame
        trade_date = date_index.date()                              # Convert the pandas Timestamp index to a plain Python date object

        open_price  = float(row["Open"])   if pd.notna(row["Open"])   else None  # Convert open to float, or None if yfinance returned NaN
        high_price  = float(row["High"])   if pd.notna(row["High"])   else None  # Convert high to float, or None if NaN
        low_price   = float(row["Low"])    if pd.notna(row["Low"])    else None  # Convert low to float, or None if NaN
        close_price = float(row["Close"])  if pd.notna(row["Close"])  else None  # Convert close to float, or None if NaN
        volume      = int(row["Volume"])   if pd.notna(row["Volume"]) else None  # Convert volume to int, or None if NaN

        ret_value = daily_returns.loc[date_index]                   # Look up the pre-calculated daily return for this specific date
        daily_ret = round(float(ret_value), 6) if pd.notna(ret_value) else None  # Round to 6 decimal places to match NUMERIC(8,6) column, or None for first row

        rows.append((                                               # Append a tuple with all columns in the exact order the INSERT expects
            ticker,                                                 # prices.ticker — foreign key to stocks.ticker
            trade_date,                                             # prices.date — calendar date of this trading day
            open_price,                                             # prices.open — opening price
            high_price,                                             # prices.high — highest price of the day
            low_price,                                              # prices.low — lowest price of the day
            close_price,                                            # prices.close — closing price
            volume,                                                 # prices.volume — total shares traded
            daily_ret,                                              # prices.daily_return — percentage return vs previous close
        ))

    if not rows:                                                    # Guard: if for some reason no rows were built, skip the insert
        return 0                                                    # Return 0 rows inserted

    insert_sql = """
        INSERT INTO prices (ticker, date, open, high, low, close, volume, daily_return)
        VALUES %s
        ON CONFLICT (ticker, date) DO NOTHING
    """                                                             # SQL: bulk insert all rows, silently skip any (ticker, date) pair that already exists

    execute_values(cursor, insert_sql, rows)                        # Execute the bulk insert in one round-trip to the database
    return len(rows)                                                # Return the number of rows we attempted to insert (includes skipped duplicates)


# ---------------------------------------------------------------------------
# MAIN FUNCTION
# Orchestrates the full fetch-and-insert pipeline for all tickers.
# ---------------------------------------------------------------------------

def main() -> None:                                                 # Entry point for running this script directly or calling from the scheduler
    """
    Runs the full data ingestion pipeline:
    1. Connect to Supabase PostgreSQL
    2. Seed the stocks table with ticker metadata
    3. Fetch 2 years of OHLCV data for each ticker from yfinance
    4. Insert price data into the prices table
    5. Commit and close the connection
    """
    print("=" * 60)                                                 # Print a separator line to make the output easy to read
    print("Stock Market Analytics — Data Fetcher")                  # Print a header to identify this script in the logs
    print("=" * 60)                                                 # Print another separator line

    db_url = build_db_url()                                         # Convert the SQLAlchemy async URL to a psycopg2-compatible URL

    print("Connecting to Supabase PostgreSQL...")                    # Inform the user that we are about to open a database connection
    conn = psycopg2.connect(db_url)                                 # Open a synchronous connection to Supabase PostgreSQL using psycopg2
    conn.autocommit = False                                         # Disable autocommit so we control when changes are committed to the database

    cursor = conn.cursor()                                          # Create a cursor object — this is what we use to send SQL statements

    try:
        seed_stocks_table(cursor)                                   # Step 1: ensure all tickers exist in the stocks table before we insert prices
        conn.commit()                                               # Commit the stocks inserts before we start on prices (in case prices step fails)
        print()                                                     # Print a blank line for visual separation in the output

        total_rows_inserted = 0                                     # Counter to track the grand total of price rows inserted across all tickers

        for i, ticker in enumerate(TICKERS, start=1):              # Iterate over every ticker, with a 1-based counter for progress display
            print(f"[{i}/{len(TICKERS)}] Fetching {ticker}...")     # Print progress: e.g. "[1/21] Fetching AAPL..."

            df = fetch_ohlcv(ticker)                                # Download this ticker's 2-year OHLCV history from Yahoo Finance

            if df.empty:                                            # If yfinance returned no data (download failed or ticker delisted)
                print(f"  Skipped {ticker} — no data available")    # Report the skip and continue to the next ticker
                continue                                            # Jump to the next iteration of the loop without inserting anything

            rows_inserted = insert_prices(cursor, ticker, df)       # Insert all price rows for this ticker into the prices table
            conn.commit()                                           # Commit this ticker's data immediately — so partial progress is saved if a later ticker fails

            total_rows_inserted += rows_inserted                    # Add this ticker's row count to the running total

            print(f"  Inserted {rows_inserted} rows for {ticker}")  # Report how many rows were inserted for this ticker

        print()                                                     # Blank line before the final summary
        print("=" * 60)                                             # Separator line
        print(f"Done! Total rows inserted: {total_rows_inserted}")  # Final summary: total rows across all tickers
        print("=" * 60)                                             # Closing separator line

    except Exception as e:
        conn.rollback()                                             # If anything went wrong, roll back ALL uncommitted changes to keep the database clean
        print(f"\nERROR: {e}")                                      # Print the error message so the developer can diagnose the problem
        raise                                                       # Re-raise the exception so APScheduler or the shell can detect the failure

    finally:
        cursor.close()                                              # Always close the cursor to free the server-side resource, even if an error occurred
        conn.close()                                                # Always close the connection to release it back to the connection pool
        print("Database connection closed.")                        # Confirm the connection was closed cleanly


# ---------------------------------------------------------------------------
# SCRIPT ENTRY POINT
# This block only runs when the file is executed directly:
#     python -m app.services.data_fetcher
# It does NOT run when the module is imported by scheduler.py or other files.
# ---------------------------------------------------------------------------

if __name__ == "__main__":                                          # Standard Python idiom: only execute the block below when running directly, not when imported
    main()                                                          # Call main() to start the full data ingestion pipeline
