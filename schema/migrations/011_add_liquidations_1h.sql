-- Migration 011: Add total_liquidations_1h column to derivatives_metrics
-- FE-4 requires a 1h liquidation window for liquidations_1h_usd cross-feature.
-- The table already has total_liquidations_24h (migration 003).
-- IF NOT EXISTS makes this idempotent.

ALTER TABLE derivatives_metrics
    ADD COLUMN IF NOT EXISTS total_liquidations_1h NUMERIC(30, 2);
