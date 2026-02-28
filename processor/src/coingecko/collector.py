"""
CoinGecko BTC Dominance Collector (DI-7).

Fetches BTC.D (BTC market cap / total crypto market cap) every 10 minutes.
BTC.D is a leading indicator for alt season: falling BTC.D → alts participating
→ RISK_ON_TREND signal strengthens; rising BTC.D → capital rotating to BTC.

Data flow:
  CoinGecko public API → market_global table (TimescaleDB) + Redis cache

Redis key written (TTL 600s — 10 minutes):
  coingecko:latest:btc_d  →  {"time": ISO, "btc_d": float}

API: public, no authentication required, 10 req/min free tier limit.
Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp
import structlog

from config import Settings
from coingecko.db import upsert_market_global

log = structlog.get_logger()

_BASE_URL = "https://api.coingecko.com/api/v3"
_ENDPOINT = "/global"
_MAX_FAILURES = 5
_REDIS_TTL = 600  # 10 minutes — stale data detected within one missed cycle


class CoinGeckoCollector:
    """
    Background service that fetches BTC Dominance every 10 minutes.

    On startup: writes the current BTC.D snapshot (no historical backfill —
    CoinGecko /global returns only the current value; historical derivation
    is deferred to EV-4).
    Each cycle: fetches /global, upserts into market_global, updates Redis cache.

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
        Main loop: collect BTC.D every coingecko_poll_interval_secs until shutdown.

        Uses time.monotonic() for drift-free timing.
        """
        log.info(
            "coingecko_collector.starting",
            interval_secs=self._settings.coingecko_poll_interval_secs,
        )

        async with aiohttp.ClientSession() as session:
            while not self._shutdown.is_set():
                cycle_start = time.monotonic()
                cycle_time = datetime.now(tz=timezone.utc)

                try:
                    await self._run_cycle(session, cycle_time)
                    self._consecutive_failures = 0
                except Exception as exc:
                    self._consecutive_failures += 1
                    log.warning(
                        "coingecko_collector.cycle_failed",
                        error=str(exc),
                        consecutive=self._consecutive_failures,
                    )
                    if self._consecutive_failures >= _MAX_FAILURES:
                        log.warning(
                            "coingecko_collector.degraded",
                            consecutive=self._consecutive_failures,
                        )

                elapsed = time.monotonic() - cycle_start
                sleep_secs = max(
                    0.0, self._settings.coingecko_poll_interval_secs - elapsed
                )
                if sleep_secs > 0:
                    await asyncio.sleep(sleep_secs)

        log.info("coingecko_collector.stopped")

    async def _run_cycle(
        self, session: aiohttp.ClientSession, cycle_time: datetime
    ) -> None:
        """Fetch current BTC.D, upsert into market_global, and update Redis cache."""
        data = await self._fetch_global(session)
        rows = _parse_global(data, cycle_time)

        if rows:
            await upsert_market_global(self._pool, rows)
            await self._cache_latest(rows)
            log.info("coingecko_collector.cycle_complete", rows=len(rows))
        else:
            log.warning(
                "coingecko_collector.no_data", cycle_time=cycle_time.isoformat()
            )

    async def _fetch_global(self, session: aiohttp.ClientSession) -> dict:
        """
        Fetch /global from CoinGecko.

        Returns parsed JSON body.
        Raises on HTTP error — caller handles per-cycle failure tracking.
        """
        url = f"{_BASE_URL}{_ENDPOINT}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _cache_latest(self, rows: list[tuple]) -> None:
        """
        Write the latest BTC.D to Redis.

        Key: coingecko:latest:btc_d
        Value: {"time": ISO, "btc_d": float}
        TTL: 600s (10 minutes).
        """
        # rows is always non-empty here (caller checks); take the single row
        row_time, btc_d = rows[-1]
        payload = json.dumps({"time": row_time.isoformat(), "btc_d": float(btc_d)})
        await self._redis.set("coingecko:latest:btc_d", payload, ex=_REDIS_TTL)


def _parse_global(data: dict, cycle_time: datetime) -> list[tuple]:
    """
    Parse raw CoinGecko /global response into upsert-ready tuples.

    API response: {"data": {"btc_dominance": float, ...}}
    Output row:   (time: datetime, btc_dominance: float)

    Returns empty list if btc_dominance is missing or None.
    Pure function — no I/O.
    """
    btc_d = data.get("data", {}).get("btc_dominance")
    if btc_d is None:
        return []
    return [(cycle_time, float(btc_d))]
