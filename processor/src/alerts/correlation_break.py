"""
CORRELATION_BREAK alert evaluator (AL-6).

Reads cross_features:latest from Redis (written by CrossFeatureEngine + FE-3)
and fires when the gap between 30-day and 7-day BTC correlations exceeds a
configurable delta threshold.

Two pairs monitored:
  - BTC-SPX: BTC correlation with S&P 500
  - BTC-DXY: BTC correlation with US Dollar Index

Two directions per pair:
  - "increasing": 7-day correlation rising above 30-day baseline
  - "decreasing": 7-day correlation falling below 30-day baseline

Both directions are evaluated every cycle so the AlertEngine persistence
counters reset correctly when conditions stop holding.

Dependency: FE-3 (Macro Stress Composite) will add correlation fields to
cross_features:latest. Until FE-3 ships, pairs with absent fields are skipped
gracefully — the evaluator is ready to run the moment FE-3 adds the data.

Feature field names (established here, FE-3 must match):
  btc_spx_correlation       — 30-day BTC/SPX rolling correlation
  btc_spx_correlation_7d    — 7-day  BTC/SPX rolling correlation
  btc_dxy_correlation       — 30-day BTC/DXY rolling correlation
  btc_dxy_correlation_7d    — 7-day  BTC/DXY rolling correlation

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
from config import Settings

log = structlog.get_logger()

_ALERT_TYPE = "CORRELATION_BREAK"

# (pair_name, field_30d, field_7d) — evaluated in order each cycle
_PAIRS: list[tuple[str, str, str]] = [
    ("BTC-SPX", "btc_spx_correlation", "btc_spx_correlation_7d"),
    ("BTC-DXY", "btc_dxy_correlation", "btc_dxy_correlation_7d"),
]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrelationBreakParams:
    """
    Trigger thresholds for CORRELATION_BREAK — loaded from thresholds.yaml.

    delta_threshold: minimum directional gap between 30d and 7d correlation
        required to set conditions_met=True. A value of 0.3 means the 7-day
        correlation must diverge from the 30-day baseline by ≥ 30 points.
    """

    delta_threshold: float

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> CorrelationBreakParams:
        cb = thresholds["correlation_break"]
        return cls(delta_threshold=cb["conditions"]["delta_threshold"])

    @classmethod
    def load(cls, thresholds_path: str) -> CorrelationBreakParams:
        with open(thresholds_path) as f:
            return cls.from_thresholds(yaml.safe_load(f))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class CorrelationBreakEvaluator:
    """
    Background service that evaluates CORRELATION_BREAK conditions every 5 minutes.

    Reads cross_features:latest from Redis. Processes both pairs (BTC-SPX, BTC-DXY)
    in a single read per cycle. Calls evaluate_and_fire for each direction of each
    pair every cycle — conditions_met=False resets the persistence counter.

    No in-memory state beyond _shutdown — all persistence/cooldown state is
    managed by AlertEngine via Redis.
    """

    def __init__(self, settings: Settings, redis: Any, engine: AlertEngine) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._params = CorrelationBreakParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: evaluate CORRELATION_BREAK every feature_interval_secs until shutdown.

        Monotonic timing avoids drift — same pattern as LeadershipRotationEvaluator.
        """
        log.info(
            "correlation_break.starting",
            interval_secs=self._settings.feature_interval_secs,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            cycle_time = datetime.now(tz=timezone.utc)
            try:
                await self._evaluate(cycle_time)
            except Exception as exc:
                log.warning("correlation_break.cycle_failed", exc_info=exc)

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("correlation_break.stopped")

    async def _evaluate(self, cycle_time: datetime) -> None:
        """
        One evaluation cycle.

        Reads cross_features:latest. For each pair, if both 30d and 7d correlation
        fields are present, evaluates both increasing and decreasing directions.
        Missing fields are skipped (FE-3 not yet running).
        """
        raw = await self._redis.get("cross_features:latest")
        if raw is None:
            log.warning(
                "correlation_break.cache_miss",
                msg="cross_features:latest not yet populated",
            )
            return

        features: dict[str, Any] = json.loads(raw)["features"]

        for pair_name, field_30d, field_7d in _PAIRS:
            corr_30d: float | None = features.get(field_30d)
            corr_7d: float | None = features.get(field_7d)

            if corr_30d is None or corr_7d is None:
                log.debug(
                    "correlation_break.skip_missing_data",
                    pair=pair_name,
                    field_30d=field_30d,
                    field_7d=field_7d,
                )
                continue

            # Signed deltas — one will be positive, the other negative
            delta_up = corr_7d - corr_30d    # positive → correlation increasing
            delta_down = corr_30d - corr_7d  # positive → correlation decreasing

            await self._engine.evaluate_and_fire(
                alert_type=_ALERT_TYPE,
                symbol=None,
                direction=f"{pair_name}_increasing",
                conditions_met=(delta_up >= self._params.delta_threshold),
                severity="MEDIUM",
                trigger_values={
                    "delta": delta_up,
                    "corr_30d": corr_30d,
                    "corr_7d": corr_7d,
                    "pair": pair_name,
                },
                context={"pair": pair_name, "direction": "increasing"},
                input_snapshot=features,
                fire_time=cycle_time,
            )

            await self._engine.evaluate_and_fire(
                alert_type=_ALERT_TYPE,
                symbol=None,
                direction=f"{pair_name}_decreasing",
                conditions_met=(delta_down >= self._params.delta_threshold),
                severity="MEDIUM",
                trigger_values={
                    "delta": delta_down,
                    "corr_30d": corr_30d,
                    "corr_7d": corr_7d,
                    "pair": pair_name,
                },
                context={"pair": pair_name, "direction": "decreasing"},
                input_snapshot=features,
                fire_time=cycle_time,
            )
