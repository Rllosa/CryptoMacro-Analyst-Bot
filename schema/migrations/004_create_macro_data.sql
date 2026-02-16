-- Migration 004: Create macro_data table
-- Stores macro economic indicators from FRED and Yahoo Finance
-- Indicators: DXY, SPX, VIX, US10Y, EFFR, etc.
-- Idempotent: DROP TABLE IF EXISTS + CREATE TABLE

DROP TABLE IF EXISTS macro_data CASCADE;

CREATE TABLE macro_data (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    indicator TEXT NOT NULL,              -- DXY, SPX, VIX, US10Y, EFFR, etc.
    value NUMERIC(20, 8) NOT NULL,        -- Indicator value
    source TEXT NOT NULL,                 -- fred, yahoo
    metadata JSONB,                       -- Optional: additional context (units, notes, etc.)
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning
    PRIMARY KEY (time, indicator, source)
);

-- Convert to hypertable partitioned by time (7 days chunks for lower frequency data)
SELECT create_hypertable(
    'macro_data',
    'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_macro_data_indicator_time
    ON macro_data (indicator, time DESC);

CREATE INDEX IF NOT EXISTS idx_macro_data_source
    ON macro_data (source, time DESC);

CREATE INDEX IF NOT EXISTS idx_macro_data_indicator_source_time
    ON macro_data (indicator, source, time DESC);

-- Verify hypertable creation
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'macro_data'
    ) THEN
        RAISE EXCEPTION 'macro_data hypertable failed to create';
    END IF;
END $$;
