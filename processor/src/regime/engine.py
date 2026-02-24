"""
RegimeClassifier — background service (FE-6).

Classifies market regime every 5 minutes by reading:
  - features:latest:btcusdt  (written by FE-1 FeatureEngine)
  - cross_features:latest     (written by FE-2 CrossFeatureEngine)

Writes results to:
  - regime_state table (TimescaleDB) — skipped when uncertain
  - regime:latest (Redis, TTL 10m) — always written

Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import structlog

from config import Settings
from regime.classifier import (
    RegimeResult,
    _build_regime_inputs,
    _compute_rv_4h_zscore,
    classify_regime,
)
from regime.config import RegimeParams
from regime.db import insert_regime

log = structlog.get_logger()

_RV_BUFFER_SIZE = 288  # 24h of 5m cycles


async def cache_regime(
    redis: Any,
    cycle_time: datetime,
    result: RegimeResult,
    inputs: dict[str, Any],
) -> None:
    """Cache regime result to regime:latest with 10-minute TTL. Always written."""
    payload = {
        "time": cycle_time.isoformat(),
        "regime": result.regime,
        "confidence": result.confidence,
        "contributing_factors": result.contributing_factors,
        "inputs": inputs,
    }
    await redis.set("regime:latest", json.dumps(payload), ex=600)


class RegimeClassifier:
    """
    Background service that classifies market regime every 5 minutes.

    Maintains an in-memory rv_1h rolling buffer (maxlen=288) to compute
    rv_4h_zscore — the primary driver for VOL_EXPANSION regime detection.

    Tracks regime transitions in-memory (_current_regime, _regime_start) to
    populate regime_duration_minutes and previous_regime in the DB row.
    """

    def __init__(self, settings: Settings, pool: Any, redis: Any) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis
        self._params = RegimeParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()
        self._rv_4h_buffer: deque[float] = deque(maxlen=_RV_BUFFER_SIZE)
        self._current_regime: str | None = None
        self._regime_start: datetime | None = None

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: classify regime every feature_interval_secs until shutdown.

        Pattern mirrors VolExpansionEvaluator.run() — elapsed-aware sleep
        to avoid drift across cycles.
        """
        log.info(
            "regime_classifier.starting",
            interval_secs=self._settings.feature_interval_secs,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            try:
                await self._run_cycle()
            except Exception:
                log.exception("regime_classifier.cycle_error")

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("regime_classifier.stopped")

    async def _run_cycle(self) -> None:
        """One classification cycle: read features → classify → write DB + Redis."""
        cycle_time = datetime.now(tz=timezone.utc)

        raw = await self._redis.get("features:latest:btcusdt")
        if raw is None:
            log.warning("regime_classifier.btc_cache_miss")
            return

        per_sym: dict[str, Any] = json.loads(raw)["features"]

        # Score against history BEFORE appending — same convention as vol_expansion.
        rv_1h = per_sym.get("rv_1h")
        if rv_1h is not None:
            rv_4h_zscore = _compute_rv_4h_zscore(self._rv_4h_buffer, rv_1h) or 0.0
            self._rv_4h_buffer.append(rv_1h)
        else:
            rv_4h_zscore = 0.0

        raw_cross = await self._redis.get("cross_features:latest")
        cross: dict[str, Any] = json.loads(raw_cross)["features"] if raw_cross else {}

        inputs = _build_regime_inputs(per_sym, cross, rv_4h_zscore, self._params)
        result = classify_regime(inputs, self._params)

        previous_regime, duration_minutes = self._update_regime_tracking(result, cycle_time)

        await insert_regime(self._pool, cycle_time, result, previous_regime, duration_minutes)
        await cache_regime(self._redis, cycle_time, result, inputs)

        log.info(
            "regime_classifier.cycle_complete",
            regime=result.regime,
            confidence=round(result.confidence, 3),
        )

    def _update_regime_tracking(
        self,
        result: RegimeResult,
        cycle_time: datetime,
    ) -> tuple[str | None, int | None]:
        """
        Update in-memory regime tracking and return (previous_regime, duration_minutes).

        On transition:    previous_regime=old, duration_minutes=duration of old regime
        On continuation:  previous_regime=None, duration_minutes=running duration
        When uncertain:   previous_regime=None, duration_minutes=None (no update)
        """
        if result.regime is None:
            return None, None

        if result.regime != self._current_regime:
            previous_regime = self._current_regime
            duration_minutes = 0
            if self._current_regime is not None and self._regime_start is not None:
                duration_minutes = int(
                    (cycle_time - self._regime_start).total_seconds() / 60
                )
            self._current_regime = result.regime
            self._regime_start = cycle_time
            return previous_regime, duration_minutes

        # Same regime continues
        duration_minutes = 0
        if self._regime_start is not None:
            duration_minutes = int(
                (cycle_time - self._regime_start).total_seconds() / 60
            )
        return None, duration_minutes
