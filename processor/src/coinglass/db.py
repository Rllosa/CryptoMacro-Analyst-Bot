from __future__ import annotations

from collections.abc import Sequence

import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# Pre-built INSERT components — hoisted at module level, never rebuilt per call.
# Columns written each cycle: the subset DI-5 populates.  Other columns (e.g.
# funding_rate_8h, long_liquidations_24h) default to NULL and are filled by
# future tasks when those fields become available from the API.
_COLS = (
    "(time, symbol, exchange, funding_rate, open_interest, "
    "total_liquidations_1h, long_account_ratio, short_account_ratio)"
)
_PH = "(%s, %s, %s, %s, %s, %s, %s, %s)"


async def upsert_derivatives(pool: AsyncConnectionPool, rows: Sequence[tuple]) -> int:
    """
    Batch-upsert derivatives rows into derivatives_metrics.

    Each row must be an 8-tuple:
      (time, symbol, exchange, funding_rate, open_interest,
       total_liquidations_1h, long_account_ratio, short_account_ratio)

    NULL values are accepted for any Optional metric column.
    ON CONFLICT updates all writable columns so the latest poll wins.
    Returns the number of rows attempted.
    """
    if not rows:
        return 0
    values_clause = ", ".join(_PH for _ in rows)
    query = (
        f"INSERT INTO derivatives_metrics {_COLS} "
        f"VALUES {values_clause} "
        "ON CONFLICT (time, symbol, exchange) DO UPDATE SET "
        "    funding_rate          = EXCLUDED.funding_rate, "
        "    open_interest         = EXCLUDED.open_interest, "
        "    total_liquidations_1h = EXCLUDED.total_liquidations_1h, "
        "    long_account_ratio    = EXCLUDED.long_account_ratio, "
        "    short_account_ratio   = EXCLUDED.short_account_ratio"
    )
    flat = [v for row in rows for v in row]
    async with pool.connection() as conn:
        await conn.execute(query, flat)
    return len(rows)
