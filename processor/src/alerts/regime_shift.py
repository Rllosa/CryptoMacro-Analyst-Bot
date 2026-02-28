"""
REGIME_SHIFT alert evaluator (AL-5).

Reads the current market regime from the Redis key written by RegimeClassifier (FE-6)
and fires alerts on two distinct events:

  1. Named regime transition: The classified regime changes from one named regime to another
     (e.g., RISK_ON_TREND → DELEVERAGING). Fires HIGH severity. Dedup direction encodes the
     transition: "{old_regime}_to_{new_regime}". Cooldown: 90 minutes per unique transition.

  2. INDETERMINATE streak: 5 or more consecutive cycles where the classifier outputs
     regime=None (confidence below uncertain_threshold). Fires MEDIUM severity with
     direction="indeterminate". Streak resets after firing so the next 5-cycle window
     starts fresh.

In-memory state:
  _current_regime: last confirmed non-None regime (baseline for transition detection)
  _uncertain_streak: consecutive cycles where regime=None

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

_ALERT_TYPE = "REGIME_SHIFT"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegimeShiftParams:
    """
    Trigger thresholds for REGIME_SHIFT — loaded from thresholds.yaml.

    min_confidence: classifier must reach this confidence for a transition to fire.
    indeterminate_streak_threshold: consecutive uncertain cycles required to fire
        the INDETERMINATE variant of the alert.
    """

    min_confidence: float
    indeterminate_streak_threshold: int

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> RegimeShiftParams:
        rs = thresholds["regime_shift"]
        return cls(
            min_confidence=rs["min_confidence"],
            indeterminate_streak_threshold=rs["indeterminate_streak_threshold"],
        )

    @classmethod
    def load(cls, thresholds_path: str) -> RegimeShiftParams:
        with open(thresholds_path) as f:
            return cls.from_thresholds(yaml.safe_load(f))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class RegimeShiftEvaluator:
    """
    Background service that evaluates REGIME_SHIFT conditions every 5 minutes.

    Reads regime:latest from Redis (written by RegimeClassifier each cycle).
    Maintains _current_regime and _uncertain_streak in memory across cycles.

    No DB reads — only Redis reads + AlertEngine.evaluate_and_fire() calls.
    AlertEngine handles persistence, cooldown, DB insert, and NATS publish.
    """

    def __init__(self, settings: Settings, redis: Any, engine: AlertEngine) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._params = RegimeShiftParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()
        self._current_regime: str | None = None
        self._uncertain_streak: int = 0

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: evaluate REGIME_SHIFT every feature_interval_secs until shutdown.

        Pattern mirrors LeadershipRotationEvaluator.run() — monotonic timing to avoid drift.
        """
        log.info(
            "regime_shift.starting",
            interval_secs=self._settings.feature_interval_secs,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            cycle_time = datetime.now(tz=timezone.utc)
            try:
                await self._evaluate_cycle(cycle_time)
            except Exception as exc:
                log.warning("regime_shift.cycle_failed", exc_info=exc)

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("regime_shift.stopped")

    async def _evaluate_cycle(self, cycle_time: datetime) -> None:
        """
        One evaluation cycle.

        Reads regime:latest from Redis. Handles two paths:
          - regime=None: increment uncertain streak; fire INDETERMINATE if threshold reached.
          - regime=str: reset streak; fire transition alert if regime changed with
            sufficient confidence.
        """
        raw = await self._redis.get("regime:latest")
        if raw is None:
            log.warning("regime_shift.cache_miss", msg="regime:latest not yet populated — classifier starting up")
            return

        data: dict[str, Any] = json.loads(raw)
        new_regime: str | None = data.get("regime")
        confidence: float = data.get("confidence", 0.0)
        inputs: dict[str, Any] = data.get("inputs") or {}

        if new_regime is None:
            await self._handle_uncertain_cycle(confidence, inputs, cycle_time)
        else:
            await self._handle_named_regime(new_regime, confidence, inputs, cycle_time)

    async def _handle_uncertain_cycle(
        self,
        confidence: float,
        inputs: dict[str, Any],
        cycle_time: datetime,
    ) -> None:
        """
        Handle a cycle where the classifier is uncertain (regime=None).

        Increments the streak counter. Fires INDETERMINATE when the streak reaches
        the configured threshold. Resets streak to 0 after firing so the next streak
        starts fresh — prevents repeated firing within the same sustained uncertainty.
        """
        self._uncertain_streak += 1
        conditions_met = self._uncertain_streak >= self._params.indeterminate_streak_threshold

        fired = await self._engine.evaluate_and_fire(
            alert_type=_ALERT_TYPE,
            symbol=None,
            direction="indeterminate",
            conditions_met=conditions_met,
            severity="MEDIUM",
            trigger_values={
                "uncertain_consecutive_cycles": self._uncertain_streak,
                "confidence": confidence,
                "indeterminate_threshold": self._params.indeterminate_streak_threshold,
            },
            context={"last_known_regime": self._current_regime},
            input_snapshot=inputs,
            fire_time=cycle_time,
        )

        if fired:
            log.info(
                "regime_shift.indeterminate_fired",
                streak=self._uncertain_streak,
            )
            self._uncertain_streak = 0

    async def _handle_named_regime(
        self,
        new_regime: str,
        confidence: float,
        inputs: dict[str, Any],
        cycle_time: datetime,
    ) -> None:
        """
        Handle a cycle where the classifier produces a named regime.

        Resets the uncertain streak. Fires a transition alert if the named regime
        has changed from the last known regime with sufficient confidence.
        On first cycle after startup (_current_regime=None), just establishes baseline.
        """
        self._uncertain_streak = 0

        is_transition = (
            self._current_regime is not None
            and self._current_regime != new_regime
            and confidence >= self._params.min_confidence
        )

        if is_transition:
            old_regime = self._current_regime
            direction = f"{old_regime}_to_{new_regime}"

            fired = await self._engine.evaluate_and_fire(
                alert_type=_ALERT_TYPE,
                symbol=None,
                direction=direction,
                conditions_met=True,
                severity="HIGH",
                trigger_values={
                    "old_regime": old_regime,
                    "new_regime": new_regime,
                    "confidence": confidence,
                },
                context={
                    "regime": new_regime,
                    "regime_confidence": confidence,
                    "previous_regime": old_regime,
                },
                input_snapshot=inputs,
                fire_time=cycle_time,
            )

            if fired:
                log.info(
                    "regime_shift.transition_fired",
                    old_regime=old_regime,
                    new_regime=new_regime,
                    confidence=round(confidence, 3),
                )

        # Always update to the latest named regime — even if confidence was too low
        # to fire. This keeps _current_regime current so the next real transition
        # is detected correctly.
        self._current_regime = new_regime
