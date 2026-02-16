#!/usr/bin/env python3
"""
CryptoMacro Analyst Bot — NATS-to-TimescaleDB Normalizer
Phase 1 (Weeks 1-2) — DI-2

Consumes candle messages from NATS JetStream (published by Rust collector)
and persists them to TimescaleDB market_candles table with deduplication.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point for processor service."""
    logger.info("CryptoMacro Processor starting...")

    # TODO (DI-2): Load configuration from .env
    # TODO (DI-2): Connect to NATS JetStream
    # TODO (DI-2): Subscribe to market.candles.* subjects
    # TODO (DI-2): Connect to TimescaleDB
    # TODO (DI-2): Implement batch insert with deduplication
    # TODO (DI-2): Implement gap detection and backfill logic
    # TODO (DI-2): Health check and graceful shutdown

    logger.info("Processor initialized (skeleton mode - no-op)")

    # Keep service running
    try:
        while True:
            await asyncio.sleep(60)
            logger.info("Processor heartbeat (skeleton mode)")
    except KeyboardInterrupt:
        logger.info("Processor shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
