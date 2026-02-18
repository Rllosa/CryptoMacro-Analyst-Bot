from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# Parameterized upsert — ON CONFLICT DO NOTHING deduplicates by (time, symbol, timeframe)
_INSERT_SQL = """
    INSERT INTO market_candles
        (time, symbol, timeframe, open, high, low, close, volume, quote_volume, num_trades)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (time, symbol, timeframe) DO NOTHING
"""


async def create_pool(dsn: str, min_size: int = 2, max_size: int = 5) -> AsyncConnectionPool:
    """Create and open an async connection pool."""
    pool = AsyncConnectionPool(conninfo=dsn, min_size=min_size, max_size=max_size)
    await pool.wait()
    return pool


async def create_pool_with_retry(dsn: str, max_retries: int = 10) -> AsyncConnectionPool:
    """Connect with exponential backoff — handles docker-compose startup race."""
    for attempt in range(1, max_retries + 1):
        try:
            pool = await create_pool(dsn)
            log.info("db.connected", attempt=attempt)
            return pool
        except Exception as exc:
            if attempt >= max_retries:
                raise
            delay = min(2 ** (attempt - 1), 30)
            log.warning(
                "db.connect_failed",
                attempt=attempt,
                error=str(exc),
                retry_in_secs=delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


async def upsert_candles(pool: AsyncConnectionPool, rows: list[tuple]) -> int:
    """
    Batch-upsert candle rows into market_candles.
    Duplicates are silently ignored (ON CONFLICT DO NOTHING).
    Returns the number of rows attempted.
    """
    if not rows:
        return 0
    async with pool.connection() as conn:
        await conn.executemany(_INSERT_SQL, rows)
    return len(rows)


async def get_last_candle_time(pool: AsyncConnectionPool, symbol: str) -> Optional[datetime]:
    """Return the most recent 1m candle timestamp for a symbol, or None."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT MAX(time) FROM market_candles WHERE symbol = %s AND timeframe = '1m'",
                (symbol,),
            )
            row = await cur.fetchone()
            return row[0] if row else None
