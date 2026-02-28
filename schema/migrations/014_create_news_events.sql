-- DI-8: Cryptopanic News Feed — news_events table
--
-- Stores high-importance crypto news headlines for async LLM classification.
-- Consumer contract: LLM-2b reads WHERE classified = FALSE, classifies, sets TRUE.
-- Rule 1.1 preserved: classification is async and never in the 5-minute alert path.
--
-- Plain table (not hypertable): BIGSERIAL PK is incompatible with TimescaleDB
-- partitioning; access pattern is by classified status, not time ranges.

CREATE TABLE IF NOT EXISTS news_events (
    id           BIGSERIAL,
    source       TEXT NOT NULL,           -- 'cryptopanic'
    headline     TEXT NOT NULL,
    url          TEXT,
    published_at TIMESTAMPTZ NOT NULL,
    currencies   TEXT[],                  -- e.g. '{BTC,ETH}' — currencies mentioned
    importance   TEXT,                    -- 'high' | 'medium'
    ingested_at  TIMESTAMPTZ DEFAULT now(),
    classified   BOOLEAN DEFAULT FALSE,   -- set TRUE by LLM-2b after classification
    PRIMARY KEY (id)
);

-- Dedup: silently skip already-seen posts across poll cycles
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_events_url
    ON news_events (url)
    WHERE url IS NOT NULL;

-- LLM-2b reads: WHERE classified = FALSE ORDER BY published_at DESC
CREATE INDEX IF NOT EXISTS idx_news_events_classified
    ON news_events (classified, published_at DESC);
