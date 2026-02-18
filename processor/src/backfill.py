from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import structlog
from psycopg_pool import AsyncConnectionPool

from config import Settings
from db import get_last_candle_time, upsert_candles

log = structlog.get_logger()

# All symbols normalizer handles — matches asset scope rule 1.5
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT"]

# Binance REST kline array column indices
_IDX_OPEN_TIME = 0
_IDX_OPEN = 1
_IDX_HIGH = 2
_IDX_LOW = 3
_IDX_CLOSE = 4
_IDX_VOLUME = 5
_IDX_QUOTE_VOL = 7
_IDX_TRADES = 8

# Binance returns max 1000 klines per request
_BINANCE_MAX_LIMIT = 1000


async def run_backfill(settings: Settings, pool: AsyncConnectionPool) -> None:
    """
    On startup: detect gaps per symbol and backfill from Binance Futures REST.
    Logs detected gaps with symbol + time range (rule 6.1).
    """
    now = datetime.now(tz=timezone.utc)
    gap_threshold = timedelta(minutes=settings.gap_threshold_minutes)

    async with aiohttp.ClientSession() as session:
        for symbol in SYMBOLS:
            await _backfill_symbol(settings, pool, session, symbol, now, gap_threshold)


async def _backfill_symbol(
    settings: Settings,
    pool: AsyncConnectionPool,
    session: aiohttp.ClientSession,
    symbol: str,
    now: datetime,
    gap_threshold: timedelta,
) -> None:
    """
    Check for a data gap for one symbol and fetch missing candles if needed.

    Queries the latest stored 1m candle time, compares it to `now`, and
    fetches from Binance REST if the gap exceeds gap_threshold.
    """
    last_time = await get_last_candle_time(pool, symbol)

    if last_time is None:
        log.info("backfill.no_data", symbol=symbol)
        return

    # Ensure tz-aware for comparison
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)

    gap = now - last_time
    if gap <= gap_threshold:
        log.info(
            "backfill.no_gap",
            symbol=symbol,
            last_time=last_time.isoformat(),
            gap_seconds=round(gap.total_seconds()),
        )
        return

    log.warning(
        "backfill.gap_detected",
        symbol=symbol,
        from_time=last_time.isoformat(),
        to_time=now.isoformat(),
        gap_minutes=round(gap.total_seconds() / 60, 1),
    )

    rows = await _fetch_klines(settings, session, symbol, last_time, now)
    if rows:
        attempted = await upsert_candles(pool, rows)
        log.info("backfill.complete", symbol=symbol, fetched=len(rows), attempted=attempted)


async def _fetch_klines(
    settings: Settings,
    session: aiohttp.ClientSession,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple]:
    """
    Fetch 1m klines from Binance Futures REST, paginating as needed.

    Returns a list of DB-ready row tuples. Stops pagination when Binance
    returns fewer than the limit (last page) or on HTTP/network error.
    """
    url = f"{settings.binance_rest_base}/fapi/v1/klines"
    end_ms = int(end.timestamp() * 1000)
    current_start_ms = int(start.timestamp() * 1000)
    rows: list[tuple] = []

    while current_start_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": current_start_ms,
            "endTime": end_ms,
            "limit": _BINANCE_MAX_LIMIT,
        }
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                resp.raise_for_status()
                data: list[list] = await resp.json()
        except aiohttp.ClientError as exc:
            log.error("backfill.fetch_failed", symbol=symbol, url=url, error=str(exc))
            break
        except asyncio.TimeoutError:
            log.error("backfill.fetch_timeout", symbol=symbol, url=url)
            break

        if not data:
            break

        for kline in data:
            rows.append(_kline_to_row(symbol, kline))

        # Advance start past the last returned candle to avoid re-fetching
        current_start_ms = int(data[-1][_IDX_OPEN_TIME]) + 60_000

        if len(data) < _BINANCE_MAX_LIMIT:
            break  # Last page reached

    return rows


def _kline_to_row(symbol: str, kline: list) -> tuple:
    """Convert a Binance REST kline array into a market_candles DB row tuple."""
    open_time_ms: int = kline[_IDX_OPEN_TIME]
    dt = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    return (
        dt,
        symbol,
        "1m",
        float(kline[_IDX_OPEN]),
        float(kline[_IDX_HIGH]),
        float(kline[_IDX_LOW]),
        float(kline[_IDX_CLOSE]),
        float(kline[_IDX_VOLUME]),
        float(kline[_IDX_QUOTE_VOL]),
        int(kline[_IDX_TRADES]),
    )


def detect_gap(last_time: Optional[datetime], gap_threshold: timedelta) -> bool:
    """Return True if the gap from last_time to now exceeds the threshold."""
    if last_time is None:
        return False
    now = datetime.now(tz=timezone.utc)
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)
    return (now - last_time) > gap_threshold
