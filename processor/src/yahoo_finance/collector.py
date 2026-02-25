"""
Yahoo Finance collector (DI-4).

Polls Yahoo Finance every yahoo_poll_interval_secs during US market hours,
writes rows to macro_data (TimescaleDB), and caches the latest value per
indicator in Redis for FE-3 to read.

Indicators: DXY, SPX, NDX, VIX, GOLD
Market hours: US equity hours (weekday, 13:30–20:00 UTC)
Redis keys: macro:latest:{indicator} (TTL 86400s)

Backfill: fetches 2y of daily data on startup (idempotent via ON CONFLICT).
Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, time as dt_time, timezone
from typing import Any

import structlog

from config import Settings
from yahoo_finance.db import upsert_macro_data

log = structlog.get_logger()

# Maps indicator name → yfinance ticker symbol
_TICKERS: dict[str, str] = {
    "DXY":  "DX-Y.NYB",
    "SPX":  "^GSPC",
    "NDX":  "^IXIC",
    "VIX":  "^VIX",
    "GOLD": "GC=F",
}

# US equity market hours in UTC (EST=+5, EDT=+4 — conservative UTC range covers both)
_MARKET_OPEN_UTC  = dt_time(13, 30)  # 9:30 AM ET
_MARKET_CLOSE_UTC = dt_time(20, 0)   # 4:00 PM ET


def _is_market_open(now: datetime) -> bool:
    """Return True if now (UTC-aware) is a weekday between 13:30–20:00 UTC."""
    return (
        now.weekday() < 5  # Mon–Fri
        and _MARKET_OPEN_UTC <= now.time() < _MARKET_CLOSE_UTC
    )


class YahooFinanceCollector:
    """
    Background service that polls Yahoo Finance every poll_interval seconds
    during US market hours and writes macro indicator data to TimescaleDB + Redis.

    Cycle:
      1. Check if US market is open (weekday + 13:30–20:00 UTC); skip if not.
      2. Fetch latest 5m bar for all tickers via yfinance (synchronous, run in thread).
      3. Upsert rows to macro_data; cache latest value per indicator in Redis.

    Graceful degradation: consecutive failures are tracked. After _MAX_FAILURES
    consecutive cycle failures a warning is logged but the service keeps running.
    Backfill: 2 years of daily data is upserted on startup (idempotent).
    """

    _MAX_FAILURES = 3
    _SOURCE = "yahoo"

    def __init__(self, settings: Settings, pool: Any, redis: Any) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis
        self._shutdown = asyncio.Event()
        self._consecutive_failures = 0

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: poll Yahoo Finance every yahoo_poll_interval_secs until shutdown.

        Backfill runs once on startup before the poll loop begins.
        Uses monotonic timing to avoid drift — sleep_secs = interval - elapsed.
        """
        log.info(
            "yahoo_finance.starting",
            interval_secs=self._settings.yahoo_poll_interval_secs,
            tickers=list(_TICKERS),
        )

        # Backfill on startup — non-blocking failure allowed
        try:
            await self._backfill()
        except Exception as exc:
            log.warning("yahoo_finance.backfill_failed", error=str(exc))

        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            now = datetime.now(tz=timezone.utc)

            if _is_market_open(now):
                try:
                    await self._run_cycle(now)
                    self._consecutive_failures = 0
                except Exception as exc:
                    self._consecutive_failures += 1
                    log.warning(
                        "yahoo_finance.cycle_failed",
                        consecutive=self._consecutive_failures,
                        error=str(exc),
                    )
                    if self._consecutive_failures >= self._MAX_FAILURES:
                        log.warning(
                            "yahoo_finance.degraded",
                            consecutive=self._consecutive_failures,
                        )
            else:
                log.debug("yahoo_finance.market_closed", skip=True)

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.yahoo_poll_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("yahoo_finance.stopped")

    async def _run_cycle(self, now: datetime) -> None:
        """Fetch latest values for all tickers, write DB + Redis."""
        rows = await asyncio.to_thread(self._fetch_latest, now)
        written = await upsert_macro_data(self._pool, rows)
        await self._cache_latest(rows)
        log.info("yahoo_finance.cycle_done", rows_written=written)

    @staticmethod
    def _fetch_latest(now: datetime) -> list[tuple]:  # noqa: ARG004
        """
        Synchronous — called via asyncio.to_thread().

        Downloads the latest 5m bar for each ticker (period="1d"), takes the last
        non-null close, and returns a list of (time, indicator, value, source) tuples.
        Bar timestamp from yfinance is used rather than `now` to reflect actual data time.
        """
        import yfinance as yf  # lazy import — keeps startup fast when not yet installed

        symbols = list(_TICKERS.values())
        df = yf.download(
            symbols,
            period="1d",
            interval="5m",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
        )
        rows: list[tuple] = []
        for indicator, yf_symbol in _TICKERS.items():
            try:
                close_series = df[yf_symbol]["Close"] if len(symbols) > 1 else df["Close"]
                clean = close_series.dropna()
                bar_time = clean.index[-1].to_pydatetime()
                rows.append((bar_time, indicator, float(clean.iloc[-1]), "yahoo"))
            except Exception as exc:
                log.warning("yahoo_finance.ticker_failed", indicator=indicator, error=str(exc))
        return rows

    async def _backfill(self) -> None:
        """
        Fetch 2 years of daily data for all tickers and upsert into macro_data.
        ON CONFLICT DO UPDATE — safe to run on every restart (idempotent).
        """
        rows = await asyncio.to_thread(self._fetch_history, "2y", "1d")
        written = await upsert_macro_data(self._pool, rows)
        log.info("yahoo_finance.backfill_done", rows_written=written)

    @staticmethod
    def _fetch_history(period: str, interval: str) -> list[tuple]:
        """Synchronous history fetch — called via asyncio.to_thread()."""
        import yfinance as yf  # lazy import

        symbols = list(_TICKERS.values())
        df = yf.download(
            symbols,
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=True,
            progress=False,
        )
        rows: list[tuple] = []
        for indicator, yf_symbol in _TICKERS.items():
            try:
                close_series = df[yf_symbol]["Close"] if len(symbols) > 1 else df["Close"]
                for bar_time, value in close_series.dropna().items():
                    rows.append((bar_time.to_pydatetime(), indicator, float(value), "yahoo"))
            except Exception as exc:
                log.warning(
                    "yahoo_finance.history_failed", indicator=indicator, error=str(exc)
                )
        return rows

    async def _cache_latest(self, rows: list[tuple]) -> None:
        """Write latest value per indicator to Redis (TTL 86400s = 24h)."""
        for row_time, indicator, value, _ in rows:
            key = f"macro:latest:{indicator.lower()}"
            payload = json.dumps({"time": row_time.isoformat(), "value": value})
            await self._redis.set(key, payload, ex=86400)
