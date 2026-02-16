-- Migration 003: Create derivatives_metrics table
-- Stores funding rates, OI, liquidations from Coinglass
-- Idempotent: DROP TABLE IF EXISTS + CREATE TABLE

DROP TABLE IF EXISTS derivatives_metrics CASCADE;

CREATE TABLE derivatives_metrics (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,

    -- Funding rate metrics
    funding_rate NUMERIC(10, 8),              -- Current funding rate (%)
    funding_rate_8h NUMERIC(10, 8),           -- Annualized 8h funding rate
    predicted_funding_rate NUMERIC(10, 8),    -- Next funding rate prediction

    -- Open interest metrics
    open_interest NUMERIC(30, 2),             -- Total OI in USD
    open_interest_change_24h NUMERIC(10, 2),  -- 24h % change in OI

    -- Position metrics
    long_short_ratio NUMERIC(10, 4),          -- Ratio of long/short positions
    long_account_ratio NUMERIC(10, 4),        -- % of accounts long
    short_account_ratio NUMERIC(10, 4),       -- % of accounts short

    -- Liquidation metrics (24h window)
    long_liquidations_24h NUMERIC(30, 2),     -- Long liquidations in USD
    short_liquidations_24h NUMERIC(30, 2),    -- Short liquidations in USD
    total_liquidations_24h NUMERIC(30, 2),    -- Total liquidations in USD

    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning
    PRIMARY KEY (time, symbol, exchange)
);

-- Convert to hypertable partitioned by time (1 day chunks)
SELECT create_hypertable(
    'derivatives_metrics',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_derivatives_metrics_symbol_time
    ON derivatives_metrics (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_derivatives_metrics_exchange
    ON derivatives_metrics (exchange, time DESC);

CREATE INDEX IF NOT EXISTS idx_derivatives_metrics_symbol_exchange_time
    ON derivatives_metrics (symbol, exchange, time DESC);

-- Verify hypertable creation
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'derivatives_metrics'
    ) THEN
        RAISE EXCEPTION 'derivatives_metrics hypertable failed to create';
    END IF;
END $$;
