"""
LEADERSHIP_ROTATION alert evaluator (AL-3).

Detects statistically significant relative strength shifts between alts and BTC.
Reads cross-asset z-scores from the Redis key written by CrossFeatureEngine (FE-2).
Single Redis read per cycle — all 3 pairs are encoded in one JSON blob.

Trigger condition: |rs_zscore| >= 2.0 for any cross-pair (ETH/BTC, SOL/BTC, HYPE/BTC)
Severity: always MEDIUM (no escalation logic for this alert type)
Directions: "{alt}_over_btc" (positive z) or "btc_over_{alt}" (negative z)
symbol: None — cross-asset alert, not per-symbol

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

_ALERT_TYPE = "LEADERSHIP_ROTATION"

# (rs_value_key, rs_zscore_key, alt_name) — order matches _ALT_PAIRS in cross_features/indicators.py
# Hoisted at module level — never rebuilt per cycle.
_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("eth_btc_rs", "eth_btc_rs_zscore", "eth"),
    ("sol_btc_rs", "sol_btc_rs_zscore", "sol"),
    ("hype_btc_rs", "hype_btc_rs_zscore", "hype"),
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeadershipRotationParams:
    """Trigger threshold for LEADERSHIP_ROTATION — loaded from thresholds.yaml."""

    rs_zscore_threshold: float  # 2.0

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> LeadershipRotationParams:
        return cls(rs_zscore_threshold=thresholds["leadership_rotation"]["conditions"]["rs_zscore"])

    @classmethod
    def load(cls, thresholds_path: str) -> LeadershipRotationParams:
        with open(thresholds_path) as f:
            return cls.from_thresholds(yaml.safe_load(f))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class LeadershipRotationEvaluator:
    """
    Background service that evaluates LEADERSHIP_ROTATION conditions every 5 minutes.

    Each cycle:
      1. Reads cross_features:latest from Redis (written by CrossFeatureEngine).
      2. For each of 3 pairs × 2 directions, calls AlertEngine.evaluate_and_fire().

    No rolling buffer needed — FE-2 already computes and caches rs_zscore directly.
    None zscore → pair skipped silently (FE-2 startup warmup).
    """

    def __init__(self, settings: Settings, redis: Any, engine: AlertEngine) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._params = LeadershipRotationParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: evaluate LEADERSHIP_ROTATION every feature_interval_secs until shutdown.

        Pattern mirrors VolExpansionEvaluator.run() — monotonic timing to avoid drift.
        """
        log.info(
            "leadership_rotation.starting",
            interval_secs=self._settings.feature_interval_secs,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            cycle_time = datetime.now(tz=timezone.utc)
            try:
                await self._evaluate(cycle_time)
            except Exception as exc:
                log.warning("leadership_rotation.cycle_failed", error=str(exc))

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("leadership_rotation.stopped")

    async def _evaluate(self, cycle_time: datetime) -> None:
        """Read cross-asset features from Redis and fire alerts for all qualifying pairs."""
        raw = await self._redis.get("cross_features:latest")
        if raw is None:
            log.warning("leadership_rotation.cache_miss")
            return

        features: dict[str, Any] = json.loads(raw)["features"]
        threshold = self._params.rs_zscore_threshold

        for rs_key, zscore_key, alt in _PAIRS:
            rs_zscore = features.get(zscore_key)
            rs_value = features.get(rs_key)

            if rs_zscore is None:
                continue

            # Positive z: alt outperforming BTC
            await self._engine.evaluate_and_fire(
                alert_type=_ALERT_TYPE,
                symbol=None,
                direction=f"{alt}_over_btc",
                conditions_met=rs_zscore >= threshold,
                severity="MEDIUM",
                trigger_values={
                    "rs_zscore": rs_zscore,
                    "rs_value": rs_value,
                    "pair": f"{alt.upper()}/BTC",
                },
                context={"leading": alt.upper(), "lagging": "BTC"},
                input_snapshot=features,
                fire_time=cycle_time,
            )

            # Negative z: BTC outperforming alt (symmetric threshold)
            await self._engine.evaluate_and_fire(
                alert_type=_ALERT_TYPE,
                symbol=None,
                direction=f"btc_over_{alt}",
                conditions_met=rs_zscore <= -threshold,
                severity="MEDIUM",
                trigger_values={
                    "rs_zscore": rs_zscore,
                    "rs_value": rs_value,
                    "pair": f"{alt.upper()}/BTC",
                },
                context={"leading": "BTC", "lagging": alt.upper()},
                input_snapshot=features,
                fire_time=cycle_time,
            )
