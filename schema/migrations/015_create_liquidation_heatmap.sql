-- DI-10: Coinglass Liquidation Heatmap
--
-- Stores top-N price-level liquidation clusters above/below current price per snapshot.
-- Forward-looking cascade risk: where forced liquidations would trigger if price moves to level.
-- Distinct from total_liquidations_1h (past liquidations, DI-5) — this is future-oriented.
--
-- Consumer contract: LLM-3b Positioning Bias reads Redis cache first; DB used for audit
-- and historical backtesting only. Rule 1.1 preserved — no LLM logic in the write path.
--
-- Access pattern: latest snapshot per symbol via Redis; historical via time-range queries.
-- Hypertable justified: time-series snapshot data, 5-minute polling cadence.

CREATE TABLE IF NOT EXISTS liquidation_heatmap (
    time            TIMESTAMPTZ    NOT NULL,
    symbol          TEXT           NOT NULL,
    price_level     NUMERIC(18, 4) NOT NULL,
    liquidation_usd NUMERIC(20, 2),
    direction       TEXT           NOT NULL,  -- 'above' | 'below' current price at snapshot time
    PRIMARY KEY (time, symbol, price_level)
);

SELECT create_hypertable('liquidation_heatmap', by_range('time'), if_not_exists => TRUE);

ALTER TABLE liquidation_heatmap SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol,direction',
    timescaledb.compress_orderby   = 'time DESC'
);
SELECT add_compression_policy('liquidation_heatmap', INTERVAL '7 days', if_not_exists => TRUE);

-- LLM-3b / feature engine reads: latest snapshot per symbol, ordered by liquidation size
CREATE INDEX IF NOT EXISTS idx_liquidation_heatmap_latest
    ON liquidation_heatmap (symbol, time DESC, direction);
