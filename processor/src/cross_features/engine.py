from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from config import Settings
from cross_features.cache import cache_cross_features
from cross_features.db import fetch_dxy_5d_ago, fetch_symbol_closes, upsert_cross_features
from cross_features.indicators import MacroStressParams, compute_all_cross_features, compute_macro_features
from features.config import FeatureParams

log = structlog.get_logger()

# Mapping from cross-feature name to the symbols it involves.
# Used to populate the assets_involved TEXT[] column in cross_features.
# Hoisted at module level — never rebuilt per cycle.
_ASSETS_INVOLVED: dict[str, list[str]] = {
    "eth_btc_rs": ["ETHUSDT", "BTCUSDT"],
    "eth_btc_rs_zscore": ["ETHUSDT", "BTCUSDT"],
    "sol_btc_rs": ["SOLUSDT", "BTCUSDT"],
    "sol_btc_rs_zscore": ["SOLUSDT", "BTCUSDT"],
    "hype_btc_rs": ["HYPEUSDT", "BTCUSDT"],
    "hype_btc_rs_zscore": ["HYPEUSDT", "BTCUSDT"],
    "macro_stress": [],
    "vix": [],
    "dxy_momentum": [],
}


class CrossFeatureEngine:
    """
    Computes cross-asset features every 5 minutes and writes to cross_features.

    Features computed each cycle:
    - eth_btc_rs, sol_btc_rs, hype_btc_rs  (RS alpha vs BTC)
    - eth_btc_rs_zscore, sol_btc_rs_zscore, hype_btc_rs_zscore
    - macro_stress, vix, dxy_momentum  (FE-3 macro overlay via Yahoo Finance)

    Runs concurrently alongside Normalizer and FeatureEngine in main.py.
    One failing cycle never crashes the service (rule 1.3).
    """

    def __init__(self, settings: Settings, pool: Any, redis: Any) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis
        self._params = FeatureParams.load(settings.thresholds_path)
        self._macro_params = MacroStressParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()
        # Total candles needed: rs_zscore_window + rs_lookback
        self._n_candles = self._params.rs_zscore_window + self._params.rs_lookback

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: compute cross features every feature_interval_secs.

        Continues until request_shutdown() is called. Cycle failures are
        logged as errors and the loop continues (graceful degradation).
        """
        log.info("cross_feature_engine.started", n_candles=self._n_candles)
        while not self._shutdown.is_set():
            cycle_time = datetime.now(tz=timezone.utc)
            _start = time.monotonic()
            try:
                await self._compute_cycle(cycle_time)
            except Exception as exc:
                log.error("cross_feature_engine.cycle_failed", error=str(exc))

            elapsed = time.monotonic() - _start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            await asyncio.sleep(sleep_secs)

        log.info("cross_feature_engine.stopped")

    async def _fetch_macro_inputs(self) -> tuple[float | None, float | None, float | None]:
        """Read VIX and DXY current from Redis; fetch DXY 5-day-ago from DB."""
        vix_raw, dxy_raw = await asyncio.gather(
            self._redis.get("macro:latest:vix"),
            self._redis.get("macro:latest:dxy"),
        )
        vix = json.loads(vix_raw)["value"] if vix_raw else None
        dxy_current = json.loads(dxy_raw)["value"] if dxy_raw else None
        dxy_5d_ago = await fetch_dxy_5d_ago(self._pool)
        return vix, dxy_current, dxy_5d_ago

    async def _compute_cycle(self, cycle_time: datetime) -> None:
        """Fetch closes, compute features, upsert to DB, cache in Redis."""
        closes = await fetch_symbol_closes(self._pool, self._n_candles)
        if closes.empty:
            log.warning("cross_feature_engine.no_data")
            return

        features = compute_all_cross_features(closes, self._params)

        # Macro overlay: replaces stub macro_stress=0.0 with real VIX+DXY composite
        vix, dxy_current, dxy_5d_ago = await self._fetch_macro_inputs()
        macro = compute_macro_features(vix, dxy_current, dxy_5d_ago, self._macro_params)
        features.update(macro)

        # NaN values are not inserted — value column is NOT NULL
        rows = [
            (cycle_time, name, value, _ASSETS_INVOLVED.get(name, []), None)
            for name, value in features.items()
            if not math.isnan(value)
        ]

        if rows:
            await upsert_cross_features(self._pool, rows)

        await cache_cross_features(self._redis, cycle_time, features)
        log.info(
            "cross_feature_engine.cycle_complete",
            features_written=len(rows),
            features_skipped=len(features) - len(rows),
        )
