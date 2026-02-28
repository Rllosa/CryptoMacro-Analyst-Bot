"""
Deribit DVOL Collector (DI-6).

Fetches the Deribit DVOL implied volatility index for BTC and ETH hourly.
DVOL is the crypto equivalent of VIX — a leading indicator for VOL_EXPANSION
and RISK_OFF_STRESS regimes.

Data flow:
  Deribit public API → deribit_dvol table (TimescaleDB) + Redis cache

Redis keys written (TTL 7200s — 2 hours):
  deribit:latest:btc  →  {"time": ISO, "close": float}
  deribit:latest:eth  →  {"time": ISO, "close": float}

API: public, no authentication required, 20 req/s rate limit.
Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import structlog

from config import Settings
from deribit.db import upsert_deribit_dvol

log = structlog.get_logger()

_BASE_URL = "https://www.deribit.com/api/v2"
_ENDPOINT = "/get_volatility_index_data"
_CURRENCIES = ("BTC", "ETH")  # rules.md §1.5 — BTC+ETH only for derivatives data
_MAX_FAILURES = 5
_BACKFILL_DAYS = 7
_REDIS_TTL = 7200  # 2 hours — stale data detected within one missed cycle


class DeribitCollector:
    """
    Background service that fetches Deribit DVOL every hour.

    On startup: backfills the last 7 days of hourly candles for BTC and ETH.
    Each cycle: fetches the last 2 hours of data (captures latest complete candle)
    and upserts into deribit_dvol. ON CONFLICT DO UPDATE makes re-runs idempotent.

    Failure handling: consecutive failure counter; degrades gracefully after
    _MAX_FAILURES without crashing the service (rule 1.3).
    """

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
        Main loop: collect DVOL every deribit_poll_interval_secs until shutdown.

        Backfills last 7 days on startup, then polls on the configured interval.
        Uses time.monotonic() for drift-free timing.
        """
        log.info(
            "deribit_collector.starting",
            interval_secs=self._settings.deribit_poll_interval_secs,
            backfill_days=_BACKFILL_DAYS,
        )

        async with aiohttp.ClientSession() as session:
            # Startup backfill — best-effort, non-fatal on failure
            try:
                await self._backfill(session)
            except Exception as exc:
                log.warning("deribit_collector.backfill_failed", error=str(exc))

            while not self._shutdown.is_set():
                cycle_start = time.monotonic()
                cycle_time = datetime.now(tz=timezone.utc)

                try:
                    await self._run_cycle(session, cycle_time)
                    self._consecutive_failures = 0
                except Exception as exc:
                    self._consecutive_failures += 1
                    log.warning(
                        "deribit_collector.cycle_failed",
                        error=str(exc),
                        consecutive=self._consecutive_failures,
                    )
                    if self._consecutive_failures >= _MAX_FAILURES:
                        log.warning(
                            "deribit_collector.degraded",
                            consecutive=self._consecutive_failures,
                        )

                elapsed = time.monotonic() - cycle_start
                sleep_secs = max(
                    0.0, self._settings.deribit_poll_interval_secs - elapsed
                )
                if sleep_secs > 0:
                    await asyncio.sleep(sleep_secs)

        log.info("deribit_collector.stopped")

    async def _backfill(self, session: aiohttp.ClientSession) -> None:
        """Fetch and upsert the last _BACKFILL_DAYS of hourly DVOL for BTC and ETH."""
        now = datetime.now(tz=timezone.utc)
        start = now - timedelta(days=_BACKFILL_DAYS)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)

        btc_rows, eth_rows = await asyncio.gather(
            self._fetch_dvol(session, "BTC", start_ms, end_ms),
            self._fetch_dvol(session, "ETH", start_ms, end_ms),
        )
        all_rows = btc_rows + eth_rows
        written = await upsert_deribit_dvol(self._pool, all_rows)
        log.info("deribit_collector.backfill_complete", rows_written=written)

    async def _run_cycle(
        self, session: aiohttp.ClientSession, cycle_time: datetime
    ) -> None:
        """Fetch the last 2 hours of DVOL for both currencies and persist."""
        # Fetch last 2 hours to ensure the latest complete candle is captured
        end_ms = int(cycle_time.timestamp() * 1000)
        start_ms = int((cycle_time - timedelta(hours=2)).timestamp() * 1000)

        btc_rows, eth_rows = await asyncio.gather(
            self._fetch_dvol(session, "BTC", start_ms, end_ms),
            self._fetch_dvol(session, "ETH", start_ms, end_ms),
        )
        all_rows = btc_rows + eth_rows

        if all_rows:
            await upsert_deribit_dvol(self._pool, all_rows)
            await self._cache_latest(all_rows)
            log.info("deribit_collector.cycle_complete", rows=len(all_rows))
        else:
            log.warning("deribit_collector.no_data", cycle_time=cycle_time.isoformat())

    async def _fetch_dvol(
        self,
        session: aiohttp.ClientSession,
        currency: str,
        start_ts_ms: int,
        end_ts_ms: int,
    ) -> list[tuple]:
        """
        Fetch DVOL OHLC candles from Deribit for one currency.

        Returns parsed rows ready for upsert_deribit_dvol.
        Raises on HTTP error — caller handles per-cycle failure tracking.
        """
        url = f"{_BASE_URL}{_ENDPOINT}"
        params = {
            "currency": currency,
            "resolution": 3600,
            "start_timestamp": start_ts_ms,
            "end_timestamp": end_ts_ms,
        }
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            body = await resp.json()

        data: list[list] = body.get("result", {}).get("data", [])
        return _parse_candles(data, currency)

    async def _cache_latest(self, rows: list[tuple]) -> None:
        """
        Write the latest close per currency to Redis.

        Picks the most recent row per currency (rows are ascending by time from API).
        Key: deribit:latest:{currency.lower()}
        Value: {"time": ISO, "close": float}
        TTL: 7200s (2 hours).
        """
        latest: dict[str, tuple] = {}
        for row in rows:
            # row = (time: datetime, currency: str, open, high, low, close)
            currency = row[1]
            if currency not in latest or row[0] > latest[currency][0]:
                latest[currency] = row

        for currency, row in latest.items():
            key = f"deribit:latest:{currency.lower()}"
            payload = json.dumps({"time": row[0].isoformat(), "close": float(row[5])})
            await self._redis.set(key, payload, ex=_REDIS_TTL)


def _parse_candles(data: list[list], currency: str) -> list[tuple]:
    """
    Parse raw Deribit DVOL API data into upsert-ready tuples.

    Each API row: [timestamp_ms, open, high, low, close]
    Each output row: (time: datetime, currency: str, open: float, high: float,
                      low: float, close: float)

    Rows with any None value are skipped (partial data from live candle).
    Pure function — no I/O.
    """
    rows: list[tuple] = []
    for entry in data:
        if len(entry) < 5:
            continue
        ts_ms, open_, high, low, close = entry[0], entry[1], entry[2], entry[3], entry[4]
        if any(v is None for v in (ts_ms, open_, high, low, close)):
            continue
        candle_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        rows.append((candle_time, currency, float(open_), float(high), float(low), float(close)))
    return rows
