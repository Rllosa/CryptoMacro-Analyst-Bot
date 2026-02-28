from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import structlog
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger()

# All symbols involved in cross-asset computation — matches asset scope rule 1.5
_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT"]

# Pre-built INSERT components — hoisted at module level, never rebuilt per call
_CROSS_COLS = "(time, feature_name, value, assets_involved, metadata)"
_CROSS_PH = "(%s, %s, %s, %s, %s)"


async def fetch_symbol_closes(pool: AsyncConnectionPool, n_candles: int) -> pd.DataFrame:
    """
    Fetch the last n_candles of 5m close prices for all 4 symbols in one query.

    Uses a window function to get exactly n_candles per symbol — one DB
    round-trip regardless of symbol count. Returns an ascending wide DataFrame.

    Returns:
        Wide DataFrame: columns = symbol names, index = time (ascending).
        Empty DataFrame if no data is found.
    """
    query = """
        SELECT time, symbol, close
        FROM (
            SELECT time, symbol, close,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY time DESC) AS rn
            FROM market_candles
            WHERE symbol = ANY(%s) AND timeframe = '5m'
        ) sub
        WHERE rn <= %s
        ORDER BY time ASC
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, (_SYMBOLS, n_candles))
            rows = await cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["time", "symbol", "close"])
    df["close"] = df["close"].astype(float)
    return df.pivot(index="time", columns="symbol", values="close")


async def fetch_dxy_5d_ago(pool: AsyncConnectionPool) -> float | None:
    """
    Return the oldest DXY value from the last 10 days (≈ 5 trading days ago).

    Querying the oldest value within the last 10 days (excluding today) gives
    approximately 5 trading days of lookback, accounting for weekends and
    US market holidays when Yahoo Finance does not update.

    Returns None if no DXY rows exist in the window (data not yet populated).
    """
    query = """
        SELECT value FROM macro_data
        WHERE indicator = 'DXY'
          AND time >= NOW() - INTERVAL '10 days'
          AND time < NOW() - INTERVAL '1 day'
        ORDER BY time ASC
        LIMIT 1
    """
    async with pool.connection() as conn:
        cur = await conn.execute(query)
        row = await cur.fetchone()
    return float(row[0]) if row else None


async def upsert_cross_features(pool: AsyncConnectionPool, rows: Sequence[tuple]) -> int:
    """
    Batch-insert cross-feature rows using a single multi-row INSERT.

    One DB round-trip regardless of row count. Duplicates silently ignored
    via ON CONFLICT DO NOTHING on the (time, feature_name) primary key.

    Returns:
        Number of rows attempted.
    """
    if not rows:
        return 0
    query = (
        f"INSERT INTO cross_features {_CROSS_COLS} "
        f"VALUES {', '.join([_CROSS_PH] * len(rows))} "
        "ON CONFLICT (time, feature_name) DO NOTHING"
    )
    flat = [v for row in rows for v in row]
    async with pool.connection() as conn:
        await conn.execute(query, flat)
    return len(rows)
