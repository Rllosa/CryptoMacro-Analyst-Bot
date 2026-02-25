from __future__ import annotations

import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Read queries — derivatives_metrics
# ---------------------------------------------------------------------------

_Q_LATEST_SNAPSHOT = """
    SELECT
        AVG(funding_rate)          AS avg_funding,
        SUM(open_interest)         AS total_oi,
        SUM(total_liquidations_1h) AS total_liq
    FROM derivatives_metrics
    WHERE symbol = %s
      AND time = (SELECT MAX(time) FROM derivatives_metrics WHERE symbol = %s)
"""

_Q_OI_1H_AGO = """
    SELECT SUM(open_interest) AS total_oi_1h_ago
    FROM derivatives_metrics
    WHERE symbol = %s
      AND time = (
          SELECT MAX(time) FROM derivatives_metrics
          WHERE symbol = %s AND time <= NOW() - INTERVAL '55 minutes'
      )
"""

_Q_FUNDING_STATS = """
    SELECT
        AVG(sub.avg_funding)    AS mean_funding,
        STDDEV(sub.avg_funding) AS std_funding,
        COUNT(*)                AS n_samples
    FROM (
        SELECT time, AVG(funding_rate) AS avg_funding
        FROM derivatives_metrics
        WHERE symbol = %s
          AND time >= NOW() - INTERVAL '%s days'
        GROUP BY time
    ) sub
"""


async def fetch_latest_snapshot(
    pool: AsyncConnectionPool,
    symbol: str,
) -> tuple[float | None, float | None, float | None]:
    """
    Fetch the most recent per-symbol aggregate from derivatives_metrics.

    Returns (avg_funding, total_oi, total_liq_1h).
    All values are None when no data exists for the symbol.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_Q_LATEST_SNAPSHOT, (symbol, symbol))
            row = await cur.fetchone()
    if row is None:
        return None, None, None
    return row[0], row[1], row[2]


async def fetch_oi_1h_ago(
    pool: AsyncConnectionPool,
    symbol: str,
) -> float | None:
    """
    Fetch total OI for a symbol from the snapshot closest to 1h ago.

    Returns None when no historical snapshot exists in the window.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_Q_OI_1H_AGO, (symbol, symbol))
            row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


async def fetch_funding_stats(
    pool: AsyncConnectionPool,
    symbol: str,
    lookback_days: int,
) -> tuple[float | None, float | None, int]:
    """
    Fetch mean, stddev, and sample count of per-snapshot average funding rates
    over the last lookback_days for a symbol.

    Returns (mean, std, n_samples).  mean and std are None when no data exists.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_Q_FUNDING_STATS, (symbol, lookback_days))
            row = await cur.fetchone()
    if row is None or row[2] == 0:
        return None, None, 0
    return row[0], row[1], int(row[2])
