-- Migration 006: Create feature tables
-- computed_features: per-asset technical indicators
-- cross_features: cross-asset correlations and relative strength
-- Idempotent: DROP TABLE IF EXISTS + CREATE TABLE

-- Table 1: Computed features (per-asset technical indicators)
DROP TABLE IF EXISTS computed_features CASCADE;

CREATE TABLE computed_features (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,                     -- BTC, ETH, SOL, HYPE
    feature_name TEXT NOT NULL,               -- rsi_14, atr_20, bollinger_upper, etc.
    value NUMERIC(30, 8) NOT NULL,
    metadata JSONB,                           -- Optional: computation params, window size, etc.
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning
    PRIMARY KEY (time, symbol, feature_name)
);

-- Convert to hypertable partitioned by time (1 day chunks)
SELECT create_hypertable(
    'computed_features',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_computed_features_symbol_time
    ON computed_features (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_computed_features_feature_name
    ON computed_features (feature_name, time DESC);

CREATE INDEX IF NOT EXISTS idx_computed_features_symbol_feature_time
    ON computed_features (symbol, feature_name, time DESC);


-- Table 2: Cross features (cross-asset correlations, relative strength)
DROP TABLE IF EXISTS cross_features CASCADE;

CREATE TABLE cross_features (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    feature_name TEXT NOT NULL,               -- btc_eth_corr_30d, btc_spx_corr_90d, etc.
    value NUMERIC(10, 6) NOT NULL,            -- Typically correlation coefficients (-1 to 1)
    assets_involved TEXT[],                   -- Array of symbols involved (e.g., ['BTC', 'ETH'])
    metadata JSONB,                           -- Optional: computation params, window size, etc.
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning
    PRIMARY KEY (time, feature_name)
);

-- Convert to hypertable partitioned by time (1 day chunks)
SELECT create_hypertable(
    'cross_features',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_cross_features_feature_name_time
    ON cross_features (feature_name, time DESC);

-- GIN index for assets_involved array queries
CREATE INDEX IF NOT EXISTS idx_cross_features_assets_involved
    ON cross_features USING GIN (assets_involved);


-- Verify hypertable creation
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'computed_features'
    ) THEN
        RAISE EXCEPTION 'computed_features hypertable failed to create';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'cross_features'
    ) THEN
        RAISE EXCEPTION 'cross_features hypertable failed to create';
    END IF;
END $$;
