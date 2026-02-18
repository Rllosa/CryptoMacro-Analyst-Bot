#!/usr/bin/env python3
"""
CryptoMacro Analyst Bot — NATS-to-TimescaleDB Normalizer
Phase 1 (Weeks 1-2) — DI-2

Entry point: loads config, runs backfill on startup, then subscribes to
NATS JetStream and persists candle messages to TimescaleDB in batches.
"""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import structlog

# Ensure src/ is on the path when run directly (must come before local imports)
sys.path.insert(0, str(Path(__file__).parent))

from backfill import run_backfill  # noqa: E402
from config import Settings  # noqa: E402
from db import create_pool_with_retry  # noqa: E402
from normalizer import Normalizer  # noqa: E402

log = structlog.get_logger()


def _configure_logging() -> None:
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
    _configure_logging()

    settings = Settings()
    log.info("processor.starting", nats_url=settings.nats_url, postgres_host=settings.postgres_host)

    # Connect to TimescaleDB with exponential-backoff retry
    pool = await create_pool_with_retry(settings.db_dsn)
    log.info("processor.db_connected")

    # Gap backfill on startup — fetch any missing 1m candles from Binance REST
    try:
        await run_backfill(settings, pool)
    except Exception as exc:
        log.warning("processor.backfill_failed", error=str(exc))

    normalizer = Normalizer(settings, pool)

    # Graceful shutdown on SIGTERM / SIGINT
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        log.info("processor.shutdown_requested")
        normalizer.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    log.info("processor.running")
    await normalizer.run()

    await pool.close()
    log.info("processor.stopped")


if __name__ == "__main__":
    asyncio.run(main())
