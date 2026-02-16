-- Migration 010: Create continuous aggregates
-- candles_5m: 5-minute aggregated candles from 1m data
-- candles_1h: 1-hour aggregated candles from 1m data
-- Idempotent: DROP MATERIALIZED VIEW IF EXISTS + CREATE MATERIALIZED VIEW

-- Continuous Aggregate 1: 5-minute candles
DROP MATERIALIZED VIEW IF EXISTS candles_5m CASCADE;

CREATE MATERIALIZED VIEW candles_5m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('5 minutes', time) AS bucket,
    symbol,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume,
    sum(quote_volume) AS quote_volume,
    sum(num_trades) AS num_trades,
    count(*) AS candle_count
FROM market_candles
WHERE timeframe = '1m'
GROUP BY bucket, symbol
WITH NO DATA;

-- Add refresh policy: refresh last 1 hour of data every 5 minutes
SELECT add_continuous_aggregate_policy(
    'candles_5m',
    start_offset => INTERVAL '1 hour',
    end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

-- Create indexes on continuous aggregate
CREATE INDEX IF NOT EXISTS idx_candles_5m_symbol_bucket
    ON candles_5m (symbol, bucket DESC);


-- Continuous Aggregate 2: 1-hour candles
DROP MATERIALIZED VIEW IF EXISTS candles_1h CASCADE;

CREATE MATERIALIZED VIEW candles_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    symbol,
    first(open, time) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, time) AS close,
    sum(volume) AS volume,
    sum(quote_volume) AS quote_volume,
    sum(num_trades) AS num_trades,
    count(*) AS candle_count
FROM market_candles
WHERE timeframe = '1m'
GROUP BY bucket, symbol
WITH NO DATA;

-- Add refresh policy: refresh last 24 hours of data every 1 hour
SELECT add_continuous_aggregate_policy(
    'candles_1h',
    start_offset => INTERVAL '24 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Create indexes on continuous aggregate
CREATE INDEX IF NOT EXISTS idx_candles_1h_symbol_bucket
    ON candles_1h (symbol, bucket DESC);


-- Verify continuous aggregates creation
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.continuous_aggregates
        WHERE view_name = 'candles_5m'
    ) THEN
        RAISE EXCEPTION 'candles_5m continuous aggregate failed to create';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.continuous_aggregates
        WHERE view_name = 'candles_1h'
    ) THEN
        RAISE EXCEPTION 'candles_1h continuous aggregate failed to create';
    END IF;
END $$;
