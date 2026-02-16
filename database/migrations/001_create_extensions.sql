-- Migration 001: Enable required PostgreSQL extensions
-- Idempotent: CREATE EXTENSION IF NOT EXISTS

-- TimescaleDB extension for time-series database functionality
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- pgcrypto extension for UUID generation (gen_random_uuid)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Verify extensions
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        RAISE EXCEPTION 'TimescaleDB extension failed to install';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        RAISE EXCEPTION 'pgcrypto extension failed to install';
    END IF;
END $$;
