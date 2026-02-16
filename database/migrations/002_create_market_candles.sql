-- Migration 002: Create market_candles table for OHLCV data
-- Stores 1m, 5m, 1h, 4h, 1d candles for BTC, ETH, SOL, HYPE
-- Idempotent: DROP TABLE IF EXISTS + CREATE TABLE

DROP TABLE IF EXISTS market_candles CASCADE;

CREATE TABLE market_candles (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open NUMERIC(20, 8) NOT NULL,
    high NUMERIC(20, 8) NOT NULL,
    low NUMERIC(20, 8) NOT NULL,
    close NUMERIC(20, 8) NOT NULL,
    volume NUMERIC(30, 8) NOT NULL,
    quote_volume NUMERIC(30, 8),
    num_trades INTEGER,
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning
    PRIMARY KEY (time, symbol, timeframe)
);

-- Convert to hypertable partitioned by time (1 day chunks)
SELECT create_hypertable(
    'market_candles',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for efficient querying by symbol and time range
CREATE INDEX IF NOT EXISTS idx_market_candles_symbol_time
    ON market_candles (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_market_candles_timeframe
    ON market_candles (timeframe, time DESC);

-- Composite index for per-asset per-timeframe queries
CREATE INDEX IF NOT EXISTS idx_market_candles_symbol_timeframe_time
    ON market_candles (symbol, timeframe, time DESC);

-- Verify hypertable creation
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'market_candles'
    ) THEN
        RAISE EXCEPTION 'market_candles hypertable failed to create';
    END IF;
END $$;
