"""Write macro indicator rows to macro_data (TimescaleDB)."""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# Pre-built INSERT components — hoisted at module level, never rebuilt per call.
_COLS = "(time, indicator, value, source)"
_PH = "(%s, %s, %s, %s)"


async def upsert_macro_data(pool: AsyncConnectionPool, rows: Sequence[tuple]) -> int:
    """
    Batch-upsert rows into macro_data.

    Each row: (time: datetime, indicator: str, value: float, source: str)
    ON CONFLICT (time, indicator, source) DO UPDATE value.
    Returns number of rows attempted.
    """
    if not rows:
        return 0
    values_clause = ", ".join(_PH for _ in rows)
    query = (
        f"INSERT INTO macro_data {_COLS} VALUES {values_clause} "
        "ON CONFLICT (time, indicator, source) DO UPDATE SET value = EXCLUDED.value"
    )
    flat = [v for row in rows for v in row]
    async with pool.connection() as conn:
        await conn.execute(query, flat)
    return len(rows)
