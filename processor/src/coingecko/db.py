"""Write CoinGecko global market data to market_global (TimescaleDB)."""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# Pre-built INSERT components — hoisted at module level, never rebuilt per call.
_COLS = "(time, btc_dominance)"
_PH = "(%s, %s)"


async def upsert_market_global(pool: AsyncConnectionPool, rows: Sequence[tuple]) -> int:
    """
    Batch-upsert rows into market_global.

    Each row: (time: datetime, btc_dominance: float)
    ON CONFLICT (time) DO UPDATE — idempotent for re-runs.
    Returns number of rows attempted.
    """
    if not rows:
        return 0
    values_clause = ", ".join(_PH for _ in rows)
    query = (
        f"INSERT INTO market_global {_COLS} VALUES {values_clause} "
        "ON CONFLICT (time) DO UPDATE SET "
        "    btc_dominance = EXCLUDED.btc_dominance"
    )
    flat = [v for row in rows for v in row]
    async with pool.connection() as conn:
        await conn.execute(query, flat)
    return len(rows)
