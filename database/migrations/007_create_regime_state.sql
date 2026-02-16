-- Migration 007: Create regime_state table
-- Stores current regime classification with 5 deterministic states
-- Regimes: RISK_ON_TREND, RISK_OFF_STRESS, CHOP_RANGE, VOL_EXPANSION, DELEVERAGING
-- Idempotent: DROP TABLE IF EXISTS + CREATE TABLE

DROP TABLE IF EXISTS regime_state CASCADE;

CREATE TABLE regime_state (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    time TIMESTAMPTZ NOT NULL,
    regime TEXT NOT NULL,                         -- Current regime
    confidence NUMERIC(3, 2) NOT NULL,            -- 0.00-1.00 confidence score
    contributing_factors JSONB,                   -- Factors determining this regime
    previous_regime TEXT,                         -- Previous regime (for transition tracking)
    regime_duration_minutes INTEGER,              -- Duration in current regime
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Primary key must include time for hypertable partitioning (one regime per timestamp)
    PRIMARY KEY (time),

    -- Constraints
    CONSTRAINT check_regime CHECK (regime IN (
        'RISK_ON_TREND',
        'RISK_OFF_STRESS',
        'CHOP_RANGE',
        'VOL_EXPANSION',
        'DELEVERAGING'
    )),
    CONSTRAINT check_confidence CHECK (confidence >= 0 AND confidence <= 1),
    CONSTRAINT check_previous_regime CHECK (
        previous_regime IS NULL OR previous_regime IN (
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
    'regime_state',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_regime_state_regime_time
    ON regime_state (regime, time DESC);

CREATE INDEX IF NOT EXISTS idx_regime_state_time
    ON regime_state (time DESC);

-- GIN index for contributing_factors JSONB queries
CREATE INDEX IF NOT EXISTS idx_regime_state_contributing_factors
    ON regime_state USING GIN (contributing_factors);


-- Verify hypertable creation
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'regime_state'
    ) THEN
        RAISE EXCEPTION 'regime_state hypertable failed to create';
    END IF;
END $$;
