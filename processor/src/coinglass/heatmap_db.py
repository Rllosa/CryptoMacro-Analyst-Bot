"""Write Coinglass liquidation heatmap snapshots to liquidation_heatmap (PostgreSQL)."""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# Pre-built INSERT components — hoisted at module level, never rebuilt per call.
_COLS = "(time, symbol, price_level, liquidation_usd, direction)"
_PH = "(%s, %s, %s, %s, %s)"


async def insert_heatmap_rows(pool: AsyncConnectionPool, rows: Sequence[tuple]) -> int:
    """
    Batch-insert heatmap snapshot rows into liquidation_heatmap.

    Each row: (time: datetime, symbol: str, price_level: float,
               liquidation_usd: float, direction: str)
    direction is 'above' or 'below' current price at snapshot time.
    ON CONFLICT (time, symbol, price_level) DO UPDATE — latest value wins.
    Returns number of rows attempted.
    """
    if not rows:
        return 0
    values_clause = ", ".join(_PH for _ in rows)
    query = (
        f"INSERT INTO liquidation_heatmap {_COLS} "
        f"VALUES {values_clause} "
        "ON CONFLICT (time, symbol, price_level) DO UPDATE SET "
        "    liquidation_usd = EXCLUDED.liquidation_usd, "
        "    direction       = EXCLUDED.direction"
    )
    flat = [v for row in rows for v in row]
    async with pool.connection() as conn:
        await conn.execute(query, flat)
    return len(rows)
