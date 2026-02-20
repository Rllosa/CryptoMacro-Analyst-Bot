#!/usr/bin/env python3
"""
CryptoMacro Analyst Bot — Processor Service
Phase 1 (Weeks 1-3) — DI-2, FE-1, FE-2, AL-1

Entry point: loads config, runs backfill on startup, then runs the
NATS-to-TimescaleDB normalizer, per-asset feature engine, and cross-asset
feature engine concurrently.  AlertEngine is initialized here and passed to
alert evaluators (AL-2+) — it has no run loop of its own.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import nats as nats_client
import redis.asyncio as aioredis
import structlog

# Ensure src/ is on the path when run directly (must come before local imports)
sys.path.insert(0, str(Path(__file__).parent))

from alerts.config import AlertParams  # noqa: E402
from alerts.engine import AlertEngine  # noqa: E402
from alerts.vol_expansion import VolExpansionEvaluator  # noqa: E402
from backfill import run_backfill  # noqa: E402
from config import Settings  # noqa: E402
from cross_features.engine import CrossFeatureEngine  # noqa: E402
from db import create_pool_with_retry  # noqa: E402
from features.engine import FeatureEngine  # noqa: E402
from normalizer import Normalizer  # noqa: E402

log = structlog.get_logger()


def _configure_logging() -> None:
    """Configure structlog for JSON output to stdout with ISO timestamps and log level filtering."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


async def main() -> None:
    """
    Service entry point: configure logging, connect to TimescaleDB and Redis,
    run startup backfill, then run the Normalizer and FeatureEngine concurrently
    until a shutdown signal is received.
    """
    _configure_logging()

    settings = Settings()
    log.info("processor.starting", nats_url=settings.nats_url, postgres_host=settings.postgres_host)

    # Connect to TimescaleDB with exponential-backoff retry
    pool = await create_pool_with_retry(settings.db_dsn)
    log.info("processor.db_connected")

    # Connect to NATS for alert publishing (AL-1+)
    nc = await nats_client.connect(settings.nats_url)
    log.info("processor.nats_connected")

    # Connect to Redis for feature caching
    redis_client = await aioredis.from_url(settings.redis_url, decode_responses=True)
    log.info("processor.redis_connected")

    # Gap backfill on startup — fetch any missing 1m candles from Binance REST
    try:
        await run_backfill(settings, pool)
    except Exception as exc:
        log.warning("processor.backfill_failed", error=str(exc))

    normalizer = Normalizer(settings, pool)
    feature_engine = FeatureEngine(settings, pool, redis_client)
    cross_engine = CrossFeatureEngine(settings, pool, redis_client)
    # AlertEngine has no run loop — AL-2+ evaluators call evaluate_and_fire() each cycle
    alert_engine = AlertEngine(pool, redis_client, nc, AlertParams.load(settings.thresholds_path))
    vol_expansion = VolExpansionEvaluator(settings, redis_client, alert_engine)

    # Graceful shutdown on SIGTERM / SIGINT — propagate to all workers
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        log.info("processor.shutdown_requested")
        normalizer.request_shutdown()
        feature_engine.request_shutdown()
        cross_engine.request_shutdown()
        vol_expansion.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    log.info("processor.running")
    await asyncio.gather(normalizer.run(), feature_engine.run(), cross_engine.run(), vol_expansion.run())

    await nc.close()
    await redis_client.aclose()
    await pool.close()
    log.info("processor.stopped")


if __name__ == "__main__":
    asyncio.run(main())
