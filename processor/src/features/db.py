from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# Fetch enough candles to cover the largest window (volume_zscore=288) plus buffer
MIN_CANDLES = 300

# Pre-built INSERT components — hoisted at module level, never rebuilt per call
_FEATURE_COLS = "(time, symbol, feature_name, value, metadata)"
_FEATURE_PH = "(%s, %s, %s, %s, %s)"


async def fetch_candles(pool: AsyncConnectionPool, symbol: str) -> pd.DataFrame:
    """
    Fetch the most recent MIN_CANDLES 5m candles for a symbol from market_candles.

    Returns a DataFrame indexed by time (UTC, ascending) with columns:
    open, high, low, close, volume — all float64.
    Returns an empty DataFrame when no rows exist for the symbol.
    """
    query = """
        SELECT time, open, high, low, close, volume
        FROM market_candles
        WHERE symbol = %s AND timeframe = '5m'
        ORDER BY time DESC
        LIMIT %s
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, (symbol, MIN_CANDLES))
            rows = await cur.fetchall()

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # Rows are DESC — reverse once to get ascending time order
    df = pd.DataFrame(
        reversed(rows),
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df.set_index("time", inplace=True)
    return df.astype(
        {"open": "float64", "high": "float64", "low": "float64", "close": "float64", "volume": "float64"}
    )


async def upsert_features(pool: AsyncConnectionPool, rows: Sequence[tuple]) -> int:
    """
    Batch-upsert feature rows into computed_features using a single multi-row INSERT.

    Each row must be a 5-tuple: (time, symbol, feature_name, value, metadata).
    Duplicates are silently ignored (ON CONFLICT DO NOTHING).
    Returns the number of rows attempted.
    """
    if not rows:
        return 0
    query = (
        f"INSERT INTO computed_features {_FEATURE_COLS} "
        f"VALUES {', '.join(_FEATURE_PH for _ in rows)} "
        "ON CONFLICT (time, symbol, feature_name) DO NOTHING"
    )
    flat = [v for row in rows for v in row]
    async with pool.connection() as conn:
        await conn.execute(query, flat)
    return len(rows)
