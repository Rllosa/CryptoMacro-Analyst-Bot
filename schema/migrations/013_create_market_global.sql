-- DI-7: CoinGecko BTC Dominance — market_global hypertable
--
-- Stores 10-minute snapshots of global crypto market metrics.
-- BTC.D = BTC market cap / total crypto market cap (leading indicator for alt season).
-- Downstream: btc_dominance added to cross_features:latest (FE task).

CREATE TABLE IF NOT EXISTS market_global (
    id            UUID DEFAULT gen_random_uuid() NOT NULL,
    time          TIMESTAMPTZ NOT NULL,
    btc_dominance NUMERIC(6, 3) NOT NULL,   -- BTC.D percentage e.g. 58.413 (range 0–100)
    created_at    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (time)
);

SELECT create_hypertable(
    'market_global', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_market_global_time
    ON market_global (time DESC);
