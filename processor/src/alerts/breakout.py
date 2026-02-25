"""
BREAKOUT alert evaluator (AL-4).

Detects price breakouts beyond 4h/24h high/low ranges with volume confirmation.
Reads per-symbol feature snapshots from the Redis cache written by FE-1.

Trigger condition: breakout flag set (1.0) AND volume_zscore >= 1.0
Severity: 24h breakout = HIGH, 4h breakout = MEDIUM
Directions: high_24h, high_4h, low_24h, low_4h (per symbol)

Exclusion logic: when a 24h breakout flag is also set, the corresponding 4h
direction gets conditions_met=False to prevent double-firing MEDIUM + HIGH
for the same breakout event.

symbol: per-symbol (BTCUSDT, ETHUSDT, SOLUSDT, HYPEUSDT)
Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
import yaml

from alerts.engine import AlertEngine
from alerts.symbol_multipliers import SymbolMultipliers
from backfill import SYMBOLS
from config import Settings

log = structlog.get_logger()

_ALERT_TYPE = "BREAKOUT"

# (direction, flag_key, exclude_if_key, is_24h)
# exclude_if_key: if that flag is also set, conditions_met=False (prevent double-fire)
# Hoisted at module level — never rebuilt per cycle.
_DIRECTIONS: tuple[tuple[str, str, str | None, bool], ...] = (
    ("high_24h", "breakout_24h_high", None, True),
    ("high_4h", "breakout_4h_high", "breakout_24h_high", False),
    ("low_24h", "breakout_24h_low", None, True),
    ("low_4h", "breakout_4h_low", "breakout_24h_low", False),
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BreakoutParams:
    """Trigger thresholds for BREAKOUT — loaded from thresholds.yaml."""

    volume_zscore_min: float  # 1.0
    severity_4h: str  # "MEDIUM"
    severity_24h: str  # "HIGH"

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> BreakoutParams:
        br = thresholds["breakout"]
        return cls(
            volume_zscore_min=br["conditions"]["volume_zscore_min"],
            severity_4h=br["severity"]["breakout_4h"],
            severity_24h=br["severity"]["breakout_24h"],
        )

    @classmethod
    def load(cls, thresholds_path: str) -> BreakoutParams:
        with open(thresholds_path) as f:
            return cls.from_thresholds(yaml.safe_load(f))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class BreakoutEvaluator:
    """
    Background service that evaluates BREAKOUT conditions every 5 minutes.

    For each symbol in SYMBOLS:
      1. Reads the latest feature snapshot from Redis (written by FeatureEngine).
      2. Evaluates 4 directions: high_24h, high_4h, low_24h, low_4h.
      3. Delegates cooldown / persistence / DB / NATS to AlertEngine.

    No rolling buffer — FE-1 already computes and caches breakout flags directly.
    4h direction is excluded when its 24h counterpart flag is also set.
    """

    def __init__(self, settings: Settings, redis: Any, engine: AlertEngine) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._params = BreakoutParams.load(settings.thresholds_path)
        self._multipliers = SymbolMultipliers.load(settings.symbols_path)
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: evaluate BREAKOUT every feature_interval_secs until shutdown.

        Pattern mirrors VolExpansionEvaluator.run() — monotonic timing to avoid drift.
        """
        log.info(
            "breakout.starting",
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
                        "breakout.symbol_failed",
                        symbol=symbol,
                        exc_info=result,
                    )

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("breakout.stopped")

    async def _evaluate_symbol(self, symbol: str, cycle_time: datetime) -> None:
        """Evaluate BREAKOUT conditions for one symbol and call the engine."""
        raw = await self._redis.get(f"features:latest:{symbol.lower()}")
        if raw is None:
            log.warning("breakout.cache_miss", symbol=symbol)
            return

        features: dict[str, Any] = json.loads(raw)["features"]
        volume_zscore = features.get("volume_zscore")

        if volume_zscore is None:
            log.warning("breakout.missing_features", symbol=symbol)
            return

        multiplier = self._multipliers.get(symbol)
        volume_ok = volume_zscore >= self._params.volume_zscore_min * multiplier

        for direction, flag_key, exclude_if_key, is_24h in _DIRECTIONS:
            breakout_flag = bool(features.get(flag_key, 0.0))
            excluded = bool(exclude_if_key and features.get(exclude_if_key, 0.0))
            conditions_met = breakout_flag and volume_ok and not excluded
            severity = self._params.severity_24h if is_24h else self._params.severity_4h

            await self._engine.evaluate_and_fire(
                alert_type=_ALERT_TYPE,
                symbol=symbol,
                direction=direction,
                conditions_met=conditions_met,
                severity=severity,
                trigger_values={
                    "volume_zscore": volume_zscore,
                    flag_key: features.get(flag_key, 0.0),
                },
                context={
                    "timeframe": "24h" if is_24h else "4h",
                    "side": "high" if "high" in direction else "low",
                    "excluded": excluded,
                },
                input_snapshot=features,
                fire_time=cycle_time,
            )
