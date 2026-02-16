#!/usr/bin/env python3
"""
CryptoMacro Analyst Bot — Discord Bot
Phase 1 (Weeks 1-2) — DEL-1, DEL-2

Discord bot for:
- Alert delivery to configured channels
- Slash commands for system queries
- Daily brief posting (2x per day)
- LLM event analysis delivery
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point for Discord bot."""
    logger.info("CryptoMacro Discord Bot starting...")

    # TODO (DEL-1): Initialize Discord bot with slash commands
    # TODO (DEL-1): Set up channel structure (#alerts-high, #alerts-all, etc.)
    # TODO (DEL-2): Implement alert embed formatter
    # TODO (DEL-1): Subscribe to NATS alerts.fired subject
    # TODO (DEL-1): Implement slash commands (/status, /alerts, /regime, etc.)
    # TODO (LLM-3, LLM-4): Subscribe to NATS analysis.complete subject
    # TODO: Health checks and graceful shutdown

    logger.info("Discord bot initialized (skeleton mode - no-op)")

    try:
        while True:
            await asyncio.sleep(60)
            logger.info("Bot heartbeat (skeleton mode)")
    except KeyboardInterrupt:
        logger.info("Bot shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
