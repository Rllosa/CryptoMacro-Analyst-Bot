-- Migration 009: Create analysis_reports table
-- Stores LLM-generated reports (daily briefs, event analysis)
-- Regular table (not hypertable) as it's document-like rather than time-series
-- Idempotent: DROP TABLE IF EXISTS + CREATE TABLE

DROP TABLE IF EXISTS analysis_reports CASCADE;

CREATE TABLE analysis_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    report_type TEXT NOT NULL,                    -- daily_brief, weekly_report, event_analysis
    title TEXT NOT NULL,
    content TEXT NOT NULL,                        -- Markdown format
    alert_ids UUID[],                             -- Alert IDs if this is event analysis
    regime_context JSONB,                         -- Regime state when report was generated
    model_used TEXT,                              -- claude-sonnet-4.5, claude-opus-4.6
    metadata JSONB,                               -- Generation params, token count, etc.

    -- Constraints
    CONSTRAINT check_report_type CHECK (report_type IN (
        'daily_brief',
        'weekly_report',
        'event_analysis'
    ))
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_analysis_reports_created_at
    ON analysis_reports (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analysis_reports_type_created_at
    ON analysis_reports (report_type, created_at DESC);

-- GIN index for alert_ids array queries
CREATE INDEX IF NOT EXISTS idx_analysis_reports_alert_ids
    ON analysis_reports USING GIN (alert_ids);

-- GIN indexes for JSONB queries
CREATE INDEX IF NOT EXISTS idx_analysis_reports_regime_context
    ON analysis_reports USING GIN (regime_context);

CREATE INDEX IF NOT EXISTS idx_analysis_reports_metadata
    ON analysis_reports USING GIN (metadata);
