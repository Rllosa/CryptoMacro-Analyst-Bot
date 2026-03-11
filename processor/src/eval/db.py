"""
EV-1: Alert outcome DB queries.

All SQL strings are hoisted at module level — never rebuilt per call.
Uses psycopg3 (psycopg_pool) with %s placeholders.
Price lookups use the candles_1h continuous aggregate view.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------

# Window that catches alerts just crossing the 4h / 12h mark each cycle
_TRACKING_WINDOW = timedelta(minutes=5)

# Tolerance for candle lookup — 1h candles refresh hourly; allow ±90 min
_PRICE_TOLERANCE = timedelta(minutes=90)

# ---------------------------------------------------------------------------
# SQL — hoisted at module level
# ---------------------------------------------------------------------------

_FETCH_4H_SQL = """
    SELECT a.id, a.time, a.symbol, a.alert_type, a.severity
    FROM alerts a
    LEFT JOIN alert_outcomes ao ON ao.alert_id = a.id
    WHERE a.time >= %s
      AND a.time <  %s
      AND ao.alert_id IS NULL
"""

_FETCH_12H_SQL = """
    SELECT ao.alert_id, ao.alert_fired_at, ao.symbol, ao.price_at_alert
    FROM alert_outcomes ao
    WHERE ao.alert_fired_at >= %s
      AND ao.alert_fired_at <  %s
      AND ao.price_12h IS NULL
"""

_FETCH_PRICE_SQL = """
    SELECT close
    FROM candles_1h
    WHERE symbol  = %s
      AND bucket >= %s
      AND bucket <  %s
    ORDER BY bucket
    LIMIT 1
"""

_OUTCOME_COLS = (
    "(alert_id, alert_fired_at, symbol, alert_type, severity,"
    " price_at_alert, price_4h, move_4h_pct)"
)
_OUTCOME_PH = "(%s, %s, %s, %s, %s, %s, %s, %s)"

_INSERT_4H_SQL = (
    f"INSERT INTO alert_outcomes {_OUTCOME_COLS} VALUES {_OUTCOME_PH} "
    "ON CONFLICT (alert_id) DO NOTHING"
)

_UPDATE_12H_SQL = (
    "UPDATE alert_outcomes "
    "SET price_12h = %s, move_12h_pct = %s "
    "WHERE alert_id = %s AND price_12h IS NULL"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_alerts_for_4h_tracking(pool: Any, now: datetime) -> list[dict]:
    """
    Return alerts that just crossed the 4h mark and have no outcome row yet.

    Window: fired_at BETWEEN (now - 4h - 5min) AND (now - 4h)
    """
    window_end = now - timedelta(hours=4)
    window_start = window_end - _TRACKING_WINDOW
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_FETCH_4H_SQL, (window_start, window_end))
            rows = await cur.fetchall()
    return [
        {"id": r[0], "time": r[1], "symbol": r[2], "alert_type": r[3], "severity": r[4]}
        for r in rows
    ]


async def fetch_alerts_for_12h_tracking(pool: Any, now: datetime) -> list[dict]:
    """
    Return alert_outcomes rows that just crossed the 12h mark (price_12h IS NULL).

    Window: alert_fired_at BETWEEN (now - 12h - 5min) AND (now - 12h)
    """
    window_end = now - timedelta(hours=12)
    window_start = window_end - _TRACKING_WINDOW
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_FETCH_12H_SQL, (window_start, window_end))
            rows = await cur.fetchall()
    return [
        {"alert_id": r[0], "alert_fired_at": r[1], "symbol": r[2], "price_at_alert": r[3]}
        for r in rows
    ]


async def fetch_price_near(pool: Any, symbol: str, target: datetime) -> float | None:
    """
    Return the close of the nearest 1h candle to target time (within ±90 min).
    Returns None if no candle exists in the tolerance window.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                _FETCH_PRICE_SQL,
                (symbol, target - _PRICE_TOLERANCE, target + _PRICE_TOLERANCE),
            )
            row = await cur.fetchone()
    return float(row[0]) if row else None


async def upsert_4h_outcome(
    pool: Any,
    *,
    alert_id: Any,
    alert_fired_at: datetime,
    symbol: str,
    alert_type: str,
    severity: str,
    price_at_alert: float,
    price_4h: float,
    move_4h_pct: float,
) -> None:
    """Insert a new outcome row with 4h data. No-op if row already exists."""
    async with pool.connection() as conn:
        await conn.execute(
            _INSERT_4H_SQL,
            (
                alert_id,
                alert_fired_at,
                symbol,
                alert_type,
                severity,
                price_at_alert,
                price_4h,
                move_4h_pct,
            ),
        )


async def update_12h_outcome(
    pool: Any,
    *,
    alert_id: Any,
    price_12h: float,
    move_12h_pct: float,
) -> None:
    """Update an existing outcome row with 12h data (only if not yet filled)."""
    async with pool.connection() as conn:
        await conn.execute(
            _UPDATE_12H_SQL,
            (price_12h, move_12h_pct, alert_id),
        )
