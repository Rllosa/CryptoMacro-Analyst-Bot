-- Migration 005: Create on-chain tables
-- BTC and ETH only (per SCOPE.md)
-- Entity-tagged exchange flows and computed on-chain features
-- Idempotent: DROP TABLE IF EXISTS + CREATE TABLE

-- Table 1: Exchange flows (entity-tagged from Glassnode/CryptoQuant)
DROP TABLE IF EXISTS onchain_exchange_flows CASCADE;

CREATE TABLE onchain_exchange_flows (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,                     -- BTC, ETH only
    exchange TEXT NOT NULL,                   -- binance, coinbase, kraken, etc.

    -- Flow metrics (in native coin units)
    inflow NUMERIC(30, 8),                    -- Coins flowing into exchange
    outflow NUMERIC(30, 8),                   -- Coins flowing out of exchange
    netflow NUMERIC(30, 8),                   -- inflow - outflow

    -- Flow metrics (in USD)
    inflow_usd NUMERIC(30, 2),
    outflow_usd NUMERIC(30, 2),
    netflow_usd NUMERIC(30, 2),

    -- Entity tagging metadata
    entity_confidence NUMERIC(3, 2),          -- 0.00-1.00 confidence in entity tagging
    source TEXT NOT NULL,                     -- glassnode, cryptoquant

    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning
    PRIMARY KEY (time, symbol, exchange, source),

    -- Constraints
    CONSTRAINT check_symbol_onchain CHECK (symbol IN ('BTC', 'ETH')),
    CONSTRAINT check_entity_confidence CHECK (entity_confidence >= 0 AND entity_confidence <= 1)
);

-- Convert to hypertable partitioned by time (1 day chunks)
SELECT create_hypertable(
    'onchain_exchange_flows',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_onchain_exchange_flows_symbol_time
    ON onchain_exchange_flows (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_onchain_exchange_flows_exchange
    ON onchain_exchange_flows (exchange, time DESC);

CREATE INDEX IF NOT EXISTS idx_onchain_exchange_flows_symbol_exchange_time
    ON onchain_exchange_flows (symbol, exchange, time DESC);


-- Table 2: Computed on-chain features
DROP TABLE IF EXISTS onchain_features CASCADE;

CREATE TABLE onchain_features (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,                     -- BTC, ETH only
    feature_name TEXT NOT NULL,               -- netflow_7d_ma, exchange_balance_change, etc.
    value NUMERIC(30, 8) NOT NULL,
    metadata JSONB,                           -- Optional: computation params, source, etc.
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning
    PRIMARY KEY (time, symbol, feature_name),

    -- Constraints
    CONSTRAINT check_symbol_onchain_features CHECK (symbol IN ('BTC', 'ETH'))
);

-- Convert to hypertable partitioned by time (1 day chunks)
SELECT create_hypertable(
    'onchain_features',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_onchain_features_symbol_time
    ON onchain_features (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_onchain_features_feature_name
    ON onchain_features (feature_name, time DESC);

CREATE INDEX IF NOT EXISTS idx_onchain_features_symbol_feature_time
    ON onchain_features (symbol, feature_name, time DESC);


-- Verify hypertable creation
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'onchain_exchange_flows'
    ) THEN
        RAISE EXCEPTION 'onchain_exchange_flows hypertable failed to create';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'onchain_features'
    ) THEN
        RAISE EXCEPTION 'onchain_features hypertable failed to create';
    END IF;
END $$;
