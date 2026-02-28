-- Migration 012: Deribit DVOL implied volatility index (DI-6)
-- Stores hourly OHLC for BTC and ETH DVOL index.
-- 7-day chunks match macro_data cadence (low-frequency hourly data).

CREATE TABLE IF NOT EXISTS deribit_dvol (
    id          UUID DEFAULT gen_random_uuid() NOT NULL,
    time        TIMESTAMPTZ NOT NULL,
    currency    TEXT NOT NULL,           -- 'BTC' or 'ETH'
    open        NUMERIC(10, 4) NOT NULL,
    high        NUMERIC(10, 4) NOT NULL,
    low         NUMERIC(10, 4) NOT NULL,
    close       NUMERIC(10, 4) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (time, currency)
);

SELECT create_hypertable(
    'deribit_dvol', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_deribit_dvol_currency_time
    ON deribit_dvol (currency, time DESC);
