#!/usr/bin/env python3
"""
CryptoMacro Analyst Bot — Discord Bot
Phase 1 (Weeks 1-2) — DEL-1, DEL-2
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import discord
import nats
import redis.asyncio as aioredis
from psycopg_pool import AsyncConnectionPool

from bot import CryptoMacroBot
from config import BotSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    try:
        settings = BotSettings()
    except Exception as e:
        logger.error("Failed to load bot settings: %s", e)
        sys.exit(1)

    pool = AsyncConnectionPool(settings.db_dsn, open=False)
    await pool.open()

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    nc = await nats.connect(settings.nats_url)

    bot = CryptoMacroBot(settings=settings, pool=pool, redis=redis, nc=nc)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, bot.request_shutdown)

    # Start NATS listener concurrently before bot.start() blocks
    nats_task = asyncio.create_task(bot.start_nats_listener())

    try:
        await bot.start(settings.discord_bot_token)
    except discord.LoginFailure as e:
        logger.error("Discord login failed (check DISCORD_BOT_TOKEN): %s", e)
    finally:
        nats_task.cancel()
        try:
            await nats_task
        except asyncio.CancelledError:
            pass
        await nc.drain()
        await redis.aclose()
        await pool.close()
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
