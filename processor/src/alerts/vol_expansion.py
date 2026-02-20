"""
VOL_EXPANSION alert evaluator (AL-2).

Reads per-symbol feature snapshots from the Redis cache written by FE-1, computes
rv_1h_zscore via an in-memory rolling buffer (FE-1 writes rv_1h raw but not z-scored),
and calls AlertEngine.evaluate_and_fire() for each symbol / direction each cycle.

Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
import yaml

from alerts.engine import AlertEngine
from backfill import SYMBOLS
from config import Settings

log = structlog.get_logger()

_ALERT_TYPE = "VOL_EXPANSION"
_RV_BUFFER_SIZE = 288       # 24 h of 5 m cycles — in-memory rolling window
_MIN_BUFFER_SAMPLES = 24    # Minimum samples before z-score is reliable


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolExpansionParams:
    """Trigger thresholds for VOL_EXPANSION — loaded from thresholds.yaml."""

    rv_1h_zscore_threshold: float   # 2.0  — base trigger
    volume_zscore_threshold: float  # 1.5  — base trigger
    high_rv_1h_zscore: float        # 2.5  — escalation to HIGH
    high_volume_zscore: float       # 2.0  — escalation to HIGH
    # HIGH also requires a 24h breakout (implicit — not a numeric threshold)

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> VolExpansionParams:
        ve = thresholds["vol_expansion"]
        esc = ve["severity"]["escalate_to_high"][0]
        return cls(
            rv_1h_zscore_threshold=ve["conditions"]["rv_1h_zscore"],
            volume_zscore_threshold=ve["conditions"]["volume_zscore"],
            high_rv_1h_zscore=esc["rv_1h_zscore"],
            high_volume_zscore=esc["volume_zscore"],
        )

    @classmethod
    def load(cls, thresholds_path: str) -> VolExpansionParams:
        with open(thresholds_path) as f:
            return cls.from_thresholds(yaml.safe_load(f))


# ---------------------------------------------------------------------------
# Pure helpers — testable in isolation
# ---------------------------------------------------------------------------


def _compute_rv_zscore(buf: deque[float], rv_1h: float) -> float | None:
    """
    Population z-score of rv_1h against the rolling buffer.

    Returns None when fewer than _MIN_BUFFER_SAMPLES entries are available
    (warmup period after service restart).  Returns 0.0 if std == 0.
    """
    if len(buf) < _MIN_BUFFER_SAMPLES:
        return None
    n = len(buf)
    mean = sum(buf) / n
    variance = sum((x - mean) ** 2 for x in buf) / n
    std = math.sqrt(variance)
    if std == 0.0:
        return 0.0
    return (rv_1h - mean) / std


def _classify_severity(
    params: VolExpansionParams,
    rv_1h_zscore: float | None,
    volume_zscore: float,
    is_24h_breakout: bool,
) -> str:
    """
    Return "HIGH" iff ALL three escalation conditions hold:
      rv_1h_zscore >= high_rv_1h_zscore AND
      volume_zscore >= high_volume_zscore AND
      is_24h_breakout (not just 4h)
    Otherwise "MEDIUM".
    """
    if (
        rv_1h_zscore is not None
        and rv_1h_zscore >= params.high_rv_1h_zscore
        and volume_zscore >= params.high_volume_zscore
        and is_24h_breakout
    ):
        return "HIGH"
    return "MEDIUM"


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class VolExpansionEvaluator:
    """
    Background service that evaluates VOL_EXPANSION conditions every 5 minutes.

    For each symbol in SYMBOLS:
      1. Reads the latest feature snapshot from Redis (written by FeatureEngine).
      2. Updates the per-symbol rv_1h rolling buffer and computes rv_1h_zscore.
      3. Evaluates "up" (high breakout) and "down" (low breakout) independently.
      4. Delegates cooldown / persistence / DB / NATS to AlertEngine.

    rv_1h_zscore is NOT available in the Redis cache (FE-1 writes rv_1h raw).
    This evaluator maintains a per-symbol deque(maxlen=288) and computes the
    population z-score inline.  The buffer resets on restart — same philosophy
    as PersistenceTracker (startup warmup accepted, not a delivery guarantee).
    """

    def __init__(self, settings: Settings, redis: Any, engine: AlertEngine) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._params = VolExpansionParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()
        self._rv_buffers: dict[str, deque[float]] = {
            sym: deque(maxlen=_RV_BUFFER_SIZE) for sym in SYMBOLS
        }

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: evaluate VOL_EXPANSION every feature_interval_secs until shutdown.

        Pattern mirrors FeatureEngine.run() — monotonic timing to avoid drift.
        """
        log.info(
            "vol_expansion.starting",
            interval_secs=self._settings.feature_interval_secs,
            symbols=SYMBOLS,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            cycle_time = datetime.now(tz=timezone.utc)

            results = await asyncio.gather(
                *(self._evaluate_symbol(sym, cycle_time) for sym in SYMBOLS),
                return_exceptions=True,
            )
            for symbol, result in zip(SYMBOLS, results):
                if isinstance(result, Exception):
                    log.warning(
                        "vol_expansion.symbol_failed",
                        symbol=symbol,
                        error=str(result),
                    )

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("vol_expansion.stopped")

    async def _evaluate_symbol(self, symbol: str, cycle_time: datetime) -> None:
        """Evaluate VOL_EXPANSION conditions for one symbol and call the engine."""
        raw = await self._redis.get(f"features:latest:{symbol.lower()}")
        if raw is None:
            log.warning("vol_expansion.cache_miss", symbol=symbol)
            return

        data = json.loads(raw)
        features: dict[str, Any] = data["features"]

        rv_1h = features.get("rv_1h")
        volume_zscore = features.get("volume_zscore")

        if rv_1h is None or volume_zscore is None:
            log.warning("vol_expansion.missing_features", symbol=symbol)
            return

        # Score rv_1h against the historical distribution BEFORE adding it to the buffer.
        # This keeps the current value out of the mean/std calculation (statistically correct).
        buf = self._rv_buffers[symbol]
        rv_1h_zscore = _compute_rv_zscore(buf, rv_1h)
        buf.append(rv_1h)

        # Evaluate "up" and "down" independently.
        for direction, breakout_any, is_24h in (
            (
                "up",
                bool(features.get("breakout_4h_high")) or bool(features.get("breakout_24h_high")),
                bool(features.get("breakout_24h_high")),
            ),
            (
                "down",
                bool(features.get("breakout_4h_low")) or bool(features.get("breakout_24h_low")),
                bool(features.get("breakout_24h_low")),
            ),
        ):
            conditions_met = (
                rv_1h_zscore is not None
                and rv_1h_zscore >= self._params.rv_1h_zscore_threshold
                and volume_zscore >= self._params.volume_zscore_threshold
                and breakout_any
            )

            severity = (
                _classify_severity(self._params, rv_1h_zscore, volume_zscore, is_24h)
                if conditions_met
                else "MEDIUM"
            )

            await self._engine.evaluate_and_fire(
                alert_type=_ALERT_TYPE,
                symbol=symbol,
                direction=direction,
                conditions_met=conditions_met,
                severity=severity,
                trigger_values={
                    "rv_1h": rv_1h,
                    "rv_1h_zscore": rv_1h_zscore,
                    "volume_zscore": volume_zscore,
                },
                context={
                    "breakout_4h": breakout_any and not is_24h,
                    "breakout_24h": is_24h,
                },
                input_snapshot=features,
                fire_time=cycle_time,
            )
