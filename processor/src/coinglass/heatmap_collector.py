"""
Coinglass Liquidation Heatmap Collector (DI-10).

Polls the Coinglass aggregated-heatmap endpoint every 5 minutes for BTC, ETH, SOL, HYPE
and stores forward-looking liquidation cascade risk data:
  - DB: liquidation_heatmap — top-N price levels above/below current price per symbol
  - Redis: liquidation_heatmap:latest:{sym}usdt — JSON payload for LLM-3b (TTL 600s)

Why this matters: shows *where* positions would be force-liquidated if price moves to a
given level. Feeds LLM-3b Positioning Bias with directional cascade risk context.

Data flow:
  Coinglass v4 API → liquidation_heatmap table (PostgreSQL) + Redis cache

Consumer contract: LLM-3b reads Redis; DB is for audit and historical backtesting.
Rule 1.1 preserved — no LLM inference in this path; ingestion only.
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
from coinglass.heatmap_db import insert_heatmap_rows

log = structlog.get_logger()

_SYMBOLS = ["BTC", "ETH", "SOL", "HYPE"]
_ENDPOINT = "/futures/liquidation/aggregated-heatmap/model3"
_MAX_FAILURES = 5
_REDIS_TTL = 600


class CoinglassHeatmapCollector:
    """
    Background service that polls Coinglass heatmap every 5 minutes for BTC, ETH, SOL, HYPE.

    Fetches aggregated (cross-exchange) liquidation levels for each symbol concurrently.
    Writes top-N price-level clusters to DB and caches the Redis payload for LLM-3b.

    Failure handling: per-symbol failures are logged and skipped (remaining symbols write).
    Consecutive full-cycle failure counter; degrades gracefully after _MAX_FAILURES (rule 1.3).
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
        Main loop: collect heatmap data every coinglass_heatmap_poll_interval_secs.

        Uses time.monotonic() for drift-free timing. On startup: immediately runs a
        cycle to warm up the Redis cache before LLM-3b starts reading.
        """
        log.info(
            "coinglass_heatmap.starting",
            interval_secs=self._settings.coinglass_heatmap_poll_interval_secs,
            top_n=self._settings.coinglass_heatmap_top_n,
            symbols=_SYMBOLS,
        )

        headers = {"CG-API-KEY": self._settings.coinglass_api_key}

        async with aiohttp.ClientSession(headers=headers) as session:
            while not self._shutdown.is_set():
                cycle_start = time.monotonic()
                cycle_time = datetime.now(tz=timezone.utc)

                try:
                    await self._run_cycle(session, cycle_time)
                    self._consecutive_failures = 0
                except Exception as exc:
                    self._consecutive_failures += 1
                    log.warning(
                        "coinglass_heatmap.cycle_failed",
                        error=str(exc),
                        consecutive=self._consecutive_failures,
                    )
                    if self._consecutive_failures >= _MAX_FAILURES:
                        log.warning(
                            "coinglass_heatmap.degraded",
                            consecutive=self._consecutive_failures,
                        )

                elapsed = time.monotonic() - cycle_start
                sleep_secs = max(
                    0.0, self._settings.coinglass_heatmap_poll_interval_secs - elapsed
                )
                if sleep_secs > 0:
                    await asyncio.sleep(sleep_secs)

        log.info("coinglass_heatmap.stopped")

    async def _run_cycle(
        self, session: aiohttp.ClientSession, cycle_time: datetime
    ) -> None:
        """
        One full poll: fetch all symbols concurrently, insert good rows, cache payloads.

        Per-symbol failures are logged and skipped — remaining symbols still write.
        """
        top_n = self._settings.coinglass_heatmap_top_n

        results = await asyncio.gather(
            *(self._fetch_symbol(session, sym) for sym in _SYMBOLS),
            return_exceptions=True,
        )

        all_rows: list[tuple] = []
        for sym, result in zip(_SYMBOLS, results):
            if isinstance(result, Exception):
                log.warning(
                    "coinglass_heatmap.symbol_failed",
                    symbol=sym,
                    error=str(result),
                )
                continue
            rows, payload = _parse_heatmap(result.get("data") or {}, sym, cycle_time, top_n)
            all_rows.extend(rows)
            if payload:
                await self._cache_latest(sym, payload)

        if all_rows:
            written = await insert_heatmap_rows(self._pool, all_rows)
            log.info("coinglass_heatmap.cycle_done", rows_written=written)
        else:
            log.info("coinglass_heatmap.no_data", cycle_time=cycle_time.isoformat())

    async def _fetch_symbol(
        self, session: aiohttp.ClientSession, symbol: str
    ) -> dict:
        """
        Fetch aggregated heatmap for one symbol.

        Raises on HTTP error — caller handles per-symbol failure tracking.
        """
        base = self._settings.coinglass_base_url
        url = f"{base}{_ENDPOINT}"
        params = {"symbol": symbol}
        timeout = aiohttp.ClientTimeout(total=30)

        async with session.get(url, params=params, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _cache_latest(self, symbol: str, payload: dict) -> None:
        """Cache the Redis payload for LLM-3b with TTL 600s."""
        key = f"liquidation_heatmap:latest:{symbol.lower()}usdt"
        await self._redis.setex(key, _REDIS_TTL, json.dumps(payload))


def _parse_heatmap(
    data: dict, symbol: str, cycle_time: datetime, top_n: int
) -> tuple[list[tuple], dict]:
    """
    Parse aggregated-heatmap/model3 data into DB rows and a Redis payload.

    data["y_axis"]: list of price level floats (one per price band)
    data["liquidation_leverage_data"]: list of [x_idx, y_idx, liq_usd] — time × price 2D grid
    data["price_candlesticks"]: list of [ts, open, high, low, close, vol] — last close = current price

    Steps:
    1. Sum liq_usd grouped by y_idx → collapse time dimension → one total per price level
    2. Get current_price from price_candlesticks[-1][4] (last close)
    3. Tag each level 'above' or 'below' current_price
    4. Keep top top_n per direction by liquidation_usd
    5. Return (rows_for_db, redis_payload)

    Guard: if y_axis, liquidation_leverage_data, or current_price is missing → ([], {})
    Pure function — no I/O.
    """
    y_axis = data.get("y_axis") or []
    liq_data = data.get("liquidation_leverage_data") or []
    candlesticks = data.get("price_candlesticks") or []

    if not y_axis or not liq_data or not candlesticks:
        return [], {}

    # Current price from the latest candlestick close (index 4)
    try:
        current_price = float(candlesticks[-1][4])
    except (IndexError, TypeError, ValueError):
        return [], {}

    # Sum liquidation USD by price level index (collapse time/x dimension)
    liq_by_y: dict[int, float] = {}
    for entry in liq_data:
        if len(entry) < 3:
            continue
        try:
            y_idx = int(entry[1])
            liq_usd = float(entry[2])
        except (TypeError, ValueError):
            continue
        liq_by_y[y_idx] = liq_by_y.get(y_idx, 0.0) + liq_usd

    # Tag each price level as above/below current price
    above: list[tuple[float, float]] = []
    below: list[tuple[float, float]] = []
    for y_idx, total_liq in liq_by_y.items():
        if y_idx >= len(y_axis):
            continue
        try:
            price = float(y_axis[y_idx])
        except (TypeError, ValueError):
            continue
        if price > current_price:
            above.append((price, total_liq))
        else:
            below.append((price, total_liq))

    # Top N per direction by liquidation USD (descending)
    above_top = sorted(above, key=lambda x: x[1], reverse=True)[:top_n]
    below_top = sorted(below, key=lambda x: x[1], reverse=True)[:top_n]

    rows: list[tuple] = []
    for price, liq in above_top:
        rows.append((cycle_time, symbol, price, liq, "above"))
    for price, liq in below_top:
        rows.append((cycle_time, symbol, price, liq, "below"))

    payload = {
        "above": [{"price_level": p, "liq_usd": l} for p, l in above_top],
        "below": [{"price_level": p, "liq_usd": l} for p, l in below_top],
        "current_price": current_price,
        "timestamp": cycle_time.isoformat(),
    }

    return rows, payload
