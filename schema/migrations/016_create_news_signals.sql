-- LLM-2b: News Signals — classified output from async news classifier
--
-- Written by NewsClassifier after LLM classification of news_events rows.
-- Read by AL-12 (NEWS_EVENT evaluator) as deterministic input — no LLM in
-- the alert path (Rule 1.1 preserved).
--
-- Consumer contract:
--   AL-12 reads: WHERE relevant = TRUE
--                  AND classified_at > now() - max_age_minutes
--                  AND classified = FALSE in news_events (already enforced upstream)
--
-- Plain table (not hypertable): access pattern is by recency/relevance,
-- not dense time-range aggregation. Indexed by classified_at DESC.

CREATE TABLE IF NOT EXISTS news_signals (
    id              BIGSERIAL PRIMARY KEY,
    news_event_id   BIGINT REFERENCES news_events(id) ON DELETE CASCADE,
    classified_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    relevant        BOOLEAN NOT NULL,
    direction       TEXT NOT NULL,      -- bullish | bearish | neutral | ambiguous
    confidence      TEXT NOT NULL,      -- high | medium | low
    event_type      TEXT NOT NULL,      -- regulatory | exploit | exchange | macro | protocol | other
    assets          TEXT[],             -- e.g. '{BTC,ETH}'
    reasoning       TEXT,               -- one-sentence LLM reasoning
    headline        TEXT NOT NULL,      -- denormalised from news_events for AL-12 convenience
    source          TEXT NOT NULL,      -- cryptopanic | theblock
    published_at    TIMESTAMPTZ NOT NULL,
    age_minutes     INT NOT NULL        -- age of headline at classification time
);

-- AL-12 reads recent relevant signals: WHERE relevant = TRUE AND classified_at > threshold
CREATE INDEX IF NOT EXISTS idx_news_signals_relevant_at
    ON news_signals (relevant, classified_at DESC);

-- Prevent duplicate classification of the same news_event row
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_signals_event_id
    ON news_signals (news_event_id);
