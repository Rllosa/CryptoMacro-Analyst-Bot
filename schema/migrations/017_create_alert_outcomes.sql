-- Migration 017: Create alert_outcomes table
-- Tracks post-alert price moves at 4h and 12h windows (EV-1)
-- One row per alert, filled lazily in two passes:
--   Pass 1 (T+4h):  INSERT with price_at_alert, price_4h, move_4h_pct
--   Pass 2 (T+12h): UPDATE to fill price_12h, move_12h_pct
--
-- No FK to alerts — alerts is a hypertable (PK includes time), so a
-- standard FK on alert_id alone is not supported by TimescaleDB.
-- alert_id is the logical foreign key, enforced by application logic.

CREATE TABLE IF NOT EXISTS alert_outcomes (
    alert_id        UUID            NOT NULL,           -- logical FK → alerts.id
    alert_fired_at  TIMESTAMPTZ     NOT NULL,           -- denormalized for range queries
    symbol          TEXT            NOT NULL,           -- BTCUSDT proxy for cross-asset alerts
    alert_type      TEXT            NOT NULL,
    severity        TEXT            NOT NULL,
    price_at_alert  NUMERIC(20, 8),                     -- 1h close at alert fire time
    price_4h        NUMERIC(20, 8),                     -- 1h close at fire_time + 4h
    price_12h       NUMERIC(20, 8),                     -- 1h close at fire_time + 12h
    move_4h_pct     NUMERIC(10, 4),                     -- (price_4h  - price_at_alert) / price_at_alert * 100
    move_12h_pct    NUMERIC(10, 4),                     -- (price_12h - price_at_alert) / price_at_alert * 100
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    PRIMARY KEY (alert_id)
);

-- Efficient lookup for the 12h tracking pass (find unfilled rows by time)
CREATE INDEX IF NOT EXISTS idx_alert_outcomes_fired_at
    ON alert_outcomes (alert_fired_at DESC);

-- Alert-type breakdown queries (EV-2)
CREATE INDEX IF NOT EXISTS idx_alert_outcomes_type_fired
    ON alert_outcomes (alert_type, alert_fired_at DESC);
