from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool

from backfill import SYMBOLS
from config import Settings
from features.cache import cache_features
from features.config import FeatureParams
from features.db import fetch_candles, upsert_features
from features.indicators import compute_all_features

log = structlog.get_logger()


class FeatureEngine:
    """
    Computes per-asset technical indicators every 5 minutes and persists them.

    Cycle:
      1. Fetch the latest MIN_CANDLES 5m candles per symbol from TimescaleDB.
      2. Compute all indicators (returns, RV, RSI, MACD, BB, ATR, EMA slope,
         volume z-score, breakout flags) via pure functions.
      3. Upsert into computed_features (single multi-row INSERT per symbol).
      4. Cache the latest snapshot in Redis (TTL 10 min).

    Graceful degradation: each symbol runs independently inside asyncio.gather
    with return_exceptions=True, so a failure for one symbol never crashes others.
    """

    def __init__(self, settings: Settings, pool: AsyncConnectionPool, redis: Any) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis
        self._shutdown = asyncio.Event()
        self._params = FeatureParams.load(settings.thresholds_path)

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: compute features every feature_interval_secs until shutdown.

        Each cycle computes all symbols concurrently. Waits for any remaining
        interval time before starting the next cycle so we don't drift.
        """
        log.info(
            "feature_engine.starting",
            interval_secs=self._settings.feature_interval_secs,
            symbols=SYMBOLS,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            cycle_time = datetime.now(tz=timezone.utc)

            results = await asyncio.gather(
                *(self._compute_symbol(s, cycle_time) for s in SYMBOLS),
                return_exceptions=True,
            )
            for symbol, result in zip(SYMBOLS, results):
                if isinstance(result, Exception):
                    log.warning(
                        "feature_engine.symbol_failed",
                        symbol=symbol,
                        error=str(result),
                    )

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("feature_engine.stopped")

    async def _compute_symbol(self, symbol: str, cycle_time: datetime) -> None:
        """Compute, persist, and cache all features for one symbol."""
        df = await fetch_candles(self._pool, symbol)

        if len(df) < self._params.bollinger_period:
            log.warning(
                "features.insufficient_data",
                symbol=symbol,
                candles=len(df),
                required=self._params.bollinger_period,
            )
            return

        features = compute_all_features(df, self._params)

        # Build EAV rows — skip NaN values so we don't insert NULL into a NOT NULL column
        rows = [
            (cycle_time, symbol, name, value, None)
            for name, value in features.items()
            if not math.isnan(value)
        ]

        attempted = await upsert_features(self._pool, rows)
        await cache_features(self._redis, symbol, cycle_time, features)

        log.info(
            "features.computed",
            symbol=symbol,
            features_written=attempted,
            features_total=len(features),
            cycle_time=cycle_time.isoformat(),
        )
