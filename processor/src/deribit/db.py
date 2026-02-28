"""Write Deribit DVOL rows to deribit_dvol (TimescaleDB)."""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# Pre-built INSERT components — hoisted at module level, never rebuilt per call.
_COLS = "(time, currency, open, high, low, close)"
_PH = "(%s, %s, %s, %s, %s, %s)"


async def upsert_deribit_dvol(pool: AsyncConnectionPool, rows: Sequence[tuple]) -> int:
    """
    Batch-upsert rows into deribit_dvol.

    Each row: (time: datetime, currency: str, open: float, high: float, low: float, close: float)
    ON CONFLICT (time, currency) DO UPDATE — idempotent for backfill re-runs.
    Returns number of rows attempted.
    """
    if not rows:
        return 0
    values_clause = ", ".join(_PH for _ in rows)
    query = (
        f"INSERT INTO deribit_dvol {_COLS} VALUES {values_clause} "
        "ON CONFLICT (time, currency) DO UPDATE SET "
        "    open  = EXCLUDED.open, "
        "    high  = EXCLUDED.high, "
        "    low   = EXCLUDED.low, "
        "    close = EXCLUDED.close"
    )
    flat = [v for row in rows for v in row]
    async with pool.connection() as conn:
        await conn.execute(query, flat)
    return len(rows)
