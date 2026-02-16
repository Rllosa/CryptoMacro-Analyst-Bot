-- Migration 008: Create alerts table
-- Stores all alert records with type, severity, and context
-- 8 alert types: 6 market alerts + 2 on-chain alerts
-- Idempotent: DROP TABLE IF EXISTS + CREATE TABLE

DROP TABLE IF EXISTS alerts CASCADE;

CREATE TABLE alerts (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,                    -- When alert was triggered
    alert_type TEXT NOT NULL,                     -- Alert type (see constraint below)
    severity TEXT NOT NULL,                       -- LOW, MEDIUM, HIGH
    symbol TEXT,                                  -- Applicable symbol (NULL for cross-asset)
    title TEXT NOT NULL,                          -- Alert title
    description TEXT NOT NULL,                    -- Alert description
    trigger_conditions JSONB,                     -- Conditions that triggered the alert
    context JSONB,                                -- Additional context data
    regime_at_trigger TEXT,                       -- Regime when alert was triggered
    discord_message_id TEXT,                      -- Discord message ID after posting
    llm_analysis_id UUID,                         -- Reference to analysis_reports if LLM triggered
    acknowledged BOOLEAN DEFAULT FALSE,           -- Whether alert was acknowledged
    acknowledged_at TIMESTAMPTZ,                  -- When alert was acknowledged
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning
    PRIMARY KEY (id, time),

    -- Constraints
    CONSTRAINT check_alert_type CHECK (alert_type IN (
        -- Market alerts (6)
        'VOL_EXPANSION',
        'LEADERSHIP_ROTATION',
        'BREAKOUT',
        'REGIME_SHIFT',
        'CORRELATION_BREAK',
        'CROWDED_LEVERAGE',
        'DELEVERAGING_EVENT',
        -- On-chain alerts (2)
        'EXCHANGE_INFLOW_RISK',
        'NETFLOW_SHIFT'
    )),
    CONSTRAINT check_severity CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
    CONSTRAINT check_regime_at_trigger CHECK (
        regime_at_trigger IS NULL OR regime_at_trigger IN (
            'RISK_ON_TREND',
            'RISK_OFF_STRESS',
            'CHOP_RANGE',
            'VOL_EXPANSION',
            'DELEVERAGING'
        )
    )
);

-- Convert to hypertable partitioned by time (1 day chunks)
SELECT create_hypertable(
    'alerts',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_alerts_time
    ON alerts (time DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_type_time
    ON alerts (alert_type, time DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_severity_time
    ON alerts (severity, time DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_symbol_time
    ON alerts (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged
    ON alerts (acknowledged, time DESC);

-- GIN indexes for JSONB queries
CREATE INDEX IF NOT EXISTS idx_alerts_trigger_conditions
    ON alerts USING GIN (trigger_conditions);

CREATE INDEX IF NOT EXISTS idx_alerts_context
    ON alerts USING GIN (context);


-- Verify hypertable creation
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'alerts'
    ) THEN
        RAISE EXCEPTION 'alerts hypertable failed to create';
    END IF;
END $$;
