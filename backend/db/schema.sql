-- ============================================================
-- STOCK MARKET ANALYTICS PLATFORM — SUPABASE POSTGRESQL SCHEMA
-- Paste this entire script into the Supabase SQL Editor and run it.
-- All tables, indexes, and views are created in a single execution.
-- ============================================================


-- ============================================================
-- TABLE: stocks
-- Stores metadata for every stock we track (20 US equities + SPY + GME).
-- This is the parent/reference table — all other tables link back to it.
-- ============================================================

CREATE TABLE IF NOT EXISTS stocks (
    ticker       VARCHAR(10)  PRIMARY KEY,                -- Stock symbol (e.g. 'AAPL'), used as the unique key across all tables
    company_name VARCHAR(100),                            -- Full legal company name (e.g. 'Apple Inc.')
    sector       VARCHAR(50),                             -- Business sector (e.g. 'Technology', 'Healthcare', 'Energy')
    market_cap   BIGINT,                                  -- Market capitalisation in USD, stored as a whole number
    exchange     VARCHAR(20),                             -- Stock exchange where it trades (e.g. 'NASDAQ', 'NYSE')
    created_at   TIMESTAMPTZ  DEFAULT NOW()               -- Timestamp of when this stock record was first inserted
);


-- ============================================================
-- TABLE: prices
-- Stores daily OHLCV (Open, High, Low, Close, Volume) price data for each stock.
-- One row per stock per trading day. Source: yfinance.
-- ============================================================

CREATE TABLE IF NOT EXISTS prices (
    id           BIGSERIAL    PRIMARY KEY,                -- Auto-incrementing unique row ID
    ticker       VARCHAR(10)  REFERENCES stocks(ticker), -- Foreign key linking this price row to the stocks table
    date         DATE         NOT NULL,                   -- The trading date this row represents (market days only)
    open         NUMERIC(12,4),                           -- Opening price at market open, stored with 4 decimal places
    high         NUMERIC(12,4),                           -- Highest price reached during the trading day
    low          NUMERIC(12,4),                           -- Lowest price reached during the trading day
    close        NUMERIC(12,4),                           -- Closing price at market close (most-used value in analytics)
    volume       BIGINT,                                  -- Total number of shares traded during the day
    daily_return NUMERIC(8,6),                            -- Percentage return for the day: (close - prev_close) / prev_close
    UNIQUE(ticker, date)                                  -- Prevents duplicate entries for the same stock on the same date
);


-- ============================================================
-- TABLE: indicators
-- Stores all computed technical indicators for each stock per day.
-- Values are calculated by our Python indicators.py service and inserted here.
-- One row per stock per trading day (same grain as prices).
-- ============================================================

CREATE TABLE IF NOT EXISTS indicators (
    id               BIGSERIAL    PRIMARY KEY,                -- Auto-incrementing unique row ID
    ticker           VARCHAR(10)  REFERENCES stocks(ticker), -- Foreign key linking to the stocks table
    date             DATE         NOT NULL,                   -- The trading date these indicators correspond to
    sma_20           NUMERIC(12,4),                           -- Simple Moving Average over 20 days: average of last 20 closing prices
    sma_50           NUMERIC(12,4),                           -- Simple Moving Average over 50 days: longer-term trend signal
    ema_20           NUMERIC(12,4),                           -- Exponential Moving Average over 20 days: more weight on recent prices
    rsi_14           NUMERIC(8,4),                            -- Relative Strength Index over 14 days: 0-100 scale, >70 overbought, <30 oversold
    macd             NUMERIC(12,6),                           -- MACD line: EMA-12 minus EMA-26, signals trend direction and momentum
    macd_signal      NUMERIC(12,6),                           -- MACD signal line: 9-day EMA of the MACD line, used for crossover signals
    bollinger_upper  NUMERIC(12,4),                           -- Bollinger upper band: SMA-20 plus 2 standard deviations
    bollinger_lower  NUMERIC(12,4),                           -- Bollinger lower band: SMA-20 minus 2 standard deviations
    bollinger_mid    NUMERIC(12,4),                           -- Bollinger midline: same as SMA-20, the centre of the bands
    sharpe_ratio     NUMERIC(8,4),                            -- Sharpe ratio: risk-adjusted return = (mean return - risk-free rate) / std dev × √252
    beta             NUMERIC(8,4),                            -- Beta vs SPY (S&P 500 ETF): measures how much the stock moves relative to the market
    drawdown         NUMERIC(8,6),                            -- Maximum drawdown: worst percentage loss from a peak to a trough in the period
    UNIQUE(ticker, date)                                      -- Prevents duplicate indicator rows for the same stock on the same date
);


-- ============================================================
-- TABLE: sentiment
-- Stores financial news headlines and their FinBERT sentiment scores.
-- One row per headline — a single stock can have multiple headlines per day.
-- Source: NewsAPI (100 calls/day free tier), model: ProsusAI/finbert.
-- ============================================================

CREATE TABLE IF NOT EXISTS sentiment (
    id               BIGSERIAL    PRIMARY KEY,                -- Auto-incrementing unique row ID
    ticker           VARCHAR(10)  REFERENCES stocks(ticker), -- Foreign key linking this headline to a specific stock
    date             DATE         NOT NULL,                   -- The date this headline was published
    headline         TEXT,                                    -- Full text of the news headline as returned by NewsAPI
    sentiment_label  VARCHAR(10),                             -- FinBERT classification result: 'positive', 'negative', or 'neutral'
    sentiment_score  NUMERIC(5,4),                            -- FinBERT confidence score: 0.0000 to 1.0000 (e.g. 0.9821 = very confident)
    source           VARCHAR(50),                             -- News source name (e.g. 'Reuters', 'Bloomberg', 'CNBC')
    UNIQUE(ticker, date, headline)                            -- Prevents the same headline being stored twice for the same stock and date
);


-- ============================================================
-- TABLE: anomalies
-- Stores anomaly detection results produced by our Isolation Forest ML model.
-- One row per detected anomaly event (a stock can have multiple anomalies per day).
-- Triggered nightly via APScheduler. Features: daily return, volume z-score, price z-score.
-- ============================================================

CREATE TABLE IF NOT EXISTS anomalies (
    id               BIGSERIAL    PRIMARY KEY,                -- Auto-incrementing unique row ID
    ticker           VARCHAR(10)  REFERENCES stocks(ticker), -- Foreign key linking this anomaly to a specific stock
    date             DATE         NOT NULL,                   -- The trading date on which the anomaly was detected
    anomaly_type     VARCHAR(20),                             -- Type of anomaly detected: 'price' (unusual price move) or 'volume' (unusual volume spike)
    z_score          NUMERIC(8,4),                            -- Statistical z-score: how many standard deviations away from the mean this data point is
    isolation_score  NUMERIC(8,4),                            -- Isolation Forest anomaly score: values closer to 1.0 indicate stronger anomalies
    flagged          BOOLEAN      DEFAULT TRUE                -- Whether this anomaly is actively flagged for display on the dashboard
);


-- ============================================================
-- TABLE: forecasts
-- Stores price forecasts generated by our ARIMA and LSTM models.
-- One row per forecasted future date per stock per model type.
-- Generated by forecast_service.py and stored for the API to serve.
-- ============================================================

CREATE TABLE IF NOT EXISTS forecasts (
    id               BIGSERIAL    PRIMARY KEY,                -- Auto-incrementing unique row ID
    ticker           VARCHAR(10)  REFERENCES stocks(ticker), -- Foreign key linking this forecast to a specific stock
    forecast_date    DATE         NOT NULL,                   -- The future date this row is predicting a price for
    predicted_close  NUMERIC(12,4),                           -- The model's predicted closing price for that future date
    lower_bound      NUMERIC(12,4),                           -- Lower bound of the 95% confidence interval around the prediction
    upper_bound      NUMERIC(12,4),                           -- Upper bound of the 95% confidence interval around the prediction
    model_type       VARCHAR(20),                             -- Which model produced this forecast: 'arima' or 'lstm'
    generated_at     TIMESTAMPTZ  DEFAULT NOW()               -- Timestamp of when this forecast was generated and stored
);


-- ============================================================
-- TABLE: clusters
-- Stores the K-Means clustering result for each stock.
-- One row per stock per clustering run (computed_at tracks when).
-- Features used: avg daily return, volatility, beta, Sharpe ratio, avg volume.
-- Preprocessing: StandardScaler normalisation. k=5 clusters via elbow method.
-- ============================================================

CREATE TABLE IF NOT EXISTS clusters (
    id           BIGSERIAL    PRIMARY KEY,                -- Auto-incrementing unique row ID
    ticker       VARCHAR(10)  REFERENCES stocks(ticker), -- Foreign key linking this cluster assignment to a specific stock
    cluster_id   INTEGER,                                 -- The cluster number (0 to 4) assigned by KMeans — groups stocks with similar risk profiles
    volatility   NUMERIC(8,4),                            -- Annualised volatility (std dev of daily returns × √252) used as a clustering feature
    avg_return   NUMERIC(8,4),                            -- Average daily return over the analysis period, used as a clustering feature
    beta         NUMERIC(8,4),                            -- Beta vs SPY used as a clustering feature (same value as in indicators table)
    sharpe       NUMERIC(8,4),                            -- Sharpe ratio used as a clustering feature
    computed_at  TIMESTAMPTZ  DEFAULT NOW()               -- Timestamp of when this clustering run was performed
);


-- ============================================================
-- TABLE: watchlists
-- Stores which stocks each user has added to their personal watchlist.
-- Links to Supabase's built-in auth.users table for user identity.
-- One row per user-stock pair.
-- ============================================================

CREATE TABLE IF NOT EXISTS watchlists (
    id        BIGSERIAL    PRIMARY KEY,                -- Auto-incrementing unique row ID
    user_id   UUID         REFERENCES auth.users(id), -- Foreign key to Supabase's auth.users table — identifies which user owns this watchlist entry
    ticker    VARCHAR(10)  REFERENCES stocks(ticker), -- Foreign key to the stocks table — which stock was added to the watchlist
    added_at  TIMESTAMPTZ  DEFAULT NOW()              -- Timestamp of when the user added this stock to their watchlist
);


-- ============================================================
-- TABLE: portfolios
-- Stores each user's stock holdings (their investment portfolio).
-- One row per holding — a user can hold the same stock multiple times at different prices.
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolios (
    id         BIGSERIAL    PRIMARY KEY,                -- Auto-incrementing unique row ID
    user_id    UUID         REFERENCES auth.users(id), -- Foreign key to Supabase's auth.users table — identifies which user owns this holding
    ticker     VARCHAR(10)  REFERENCES stocks(ticker), -- Foreign key to the stocks table — which stock is held
    shares     NUMERIC(10,4),                           -- Number of shares held, supports fractional shares (e.g. 1.5000 shares)
    buy_price  NUMERIC(12,4),                           -- Price per share at time of purchase, used to calculate P&L
    buy_date   DATE                                     -- Date the position was opened, used for time-weighted return calculations
);


-- ============================================================
-- INDEXES
-- Indexes dramatically speed up the most common query patterns:
-- "give me all data for ticker X ordered by most recent date first"
-- Without indexes, PostgreSQL would scan every row in the table.
-- ============================================================

-- Speeds up queries like: SELECT * FROM prices WHERE ticker = 'AAPL' ORDER BY date DESC
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date
    ON prices(ticker, date DESC);

-- Speeds up queries like: SELECT * FROM indicators WHERE ticker = 'MSFT' ORDER BY date DESC
CREATE INDEX IF NOT EXISTS idx_indicators_ticker_date
    ON indicators(ticker, date DESC);

-- Speeds up queries like: SELECT * FROM sentiment WHERE ticker = 'GOOGL' ORDER BY date DESC
CREATE INDEX IF NOT EXISTS idx_sentiment_ticker_date
    ON sentiment(ticker, date DESC);

-- Speeds up queries like: SELECT * FROM anomalies WHERE ticker = 'GME' ORDER BY date DESC
CREATE INDEX IF NOT EXISTS idx_anomalies_ticker_date
    ON anomalies(ticker, date DESC);

-- Speeds up portfolio queries filtered by user: SELECT * FROM portfolios WHERE user_id = '...'
CREATE INDEX IF NOT EXISTS idx_portfolios_user_id
    ON portfolios(user_id);

-- Speeds up watchlist queries filtered by user: SELECT * FROM watchlists WHERE user_id = '...'
CREATE INDEX IF NOT EXISTS idx_watchlists_user_id
    ON watchlists(user_id);

-- Speeds up forecast queries: SELECT * FROM forecasts WHERE ticker = 'AAPL' AND model_type = 'arima'
CREATE INDEX IF NOT EXISTS idx_forecasts_ticker_model
    ON forecasts(ticker, model_type, forecast_date DESC);


-- ============================================================
-- VIEW: latest_indicators
-- Returns the single most recent row of indicators for every stock.
-- Used by the dashboard to show current indicator values without
-- the caller needing to know the latest date per ticker.
-- DISTINCT ON (ticker) keeps only the first row per ticker group.
-- ORDER BY ticker, date DESC ensures "first" means "most recent date".
-- ============================================================

CREATE OR REPLACE VIEW latest_indicators AS
SELECT DISTINCT ON (ticker)  -- Keep only one row per ticker (the most recent one)
    *                        -- Return all columns from the indicators table
FROM indicators              -- Source table containing all historical indicator rows
ORDER BY ticker, date DESC;  -- Order so that for each ticker the latest date comes first


-- ============================================================
-- VIEW: sentiment_summary
-- Aggregates all individual headline sentiment rows into one summary row
-- per stock per day. The frontend uses this to show a daily sentiment
-- gauge rather than listing every individual headline.
-- ============================================================

CREATE OR REPLACE VIEW sentiment_summary AS
SELECT
    ticker,                                                                  -- The stock ticker this summary row belongs to
    date,                                                                    -- The trading date this summary covers
    AVG(sentiment_score)                              AS avg_score,          -- Average FinBERT confidence score across all headlines for this ticker+date
    COUNT(*) FILTER (WHERE sentiment_label = 'positive') AS positive_count, -- Number of headlines classified as positive sentiment
    COUNT(*) FILTER (WHERE sentiment_label = 'negative') AS negative_count, -- Number of headlines classified as negative sentiment
    COUNT(*) FILTER (WHERE sentiment_label = 'neutral')  AS neutral_count,  -- Number of headlines classified as neutral sentiment
    COUNT(*)                                          AS total_headlines     -- Total number of headlines processed for this ticker+date
FROM sentiment                                                               -- Source table containing one row per headline
GROUP BY ticker, date;                                                       -- Collapse all headlines for the same stock and date into one summary row


-- ============================================================
-- END OF SCHEMA SCRIPT
-- Run order is correct: stocks must exist before any other table
-- references it via foreign key. Views are created last since they
-- depend on the tables above being present.
-- ============================================================
