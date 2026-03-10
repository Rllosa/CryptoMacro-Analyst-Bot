"""
AL-7: CROWDED_LEVERAGE Alert Evaluator (SOLO-50)

Deterministic evaluator — no LLM in the trigger path (Rule 1.1 preserved).

Trigger conditions (all must hold, all thresholds from thresholds.yaml):
  - funding_zscore  >= funding_zscore_threshold  (default: 2.0 — funding elevated vs history)
  - oi_change_pct   >= oi_change_pct_threshold   (default: 5.0 — OI growing, new leverage added)

Severity:
  HIGH   — funding_zscore >= funding_zscore_high  (default: 3.0, extreme crowding)
  MEDIUM — base trigger met but below HIGH threshold

Cooldown: 60 minutes per symbol.
Persistence: 2 cycles — crowded conditions must be sustained, not a single spike.

Reads only derivatives:latest:{sym} Redis cache (funding_zscore, oi_change_pct).
No EventAnalyzer hook — CROWDED_LEVERAGE is a warning signal, not a cascade event.
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
from backfill import SYMBOLS
from config import Settings

log = structlog.get_logger()

_ALERT_TYPE = "CROWDED_LEVERAGE"

# Redis key template — module-level, not rebuilt per call
_KEY_DERIVATIVES = "derivatives:latest:{sym}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrowdedLeverageParams:
    """Trigger thresholds for CROWDED_LEVERAGE — loaded from thresholds.yaml."""

    funding_zscore_threshold: float   # 2.0 — base trigger
    funding_zscore_high: float        # 3.0 — escalates to HIGH severity
    oi_change_pct_threshold: float    # 5.0 — OI must be growing

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> CrowdedLeverageParams:
        cfg = thresholds.get("crowded_leverage", {})
        return cls(
            funding_zscore_threshold=float(cfg.get("funding_zscore_threshold", 2.0)),
            funding_zscore_high=float(cfg.get("funding_zscore_high", 3.0)),
            oi_change_pct_threshold=float(cfg.get("oi_change_pct_threshold", 5.0)),
        )

    @classmethod
    def load(cls, thresholds_path: str) -> CrowdedLeverageParams:
        with open(thresholds_path) as fh:
            return cls.from_thresholds(yaml.safe_load(fh))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class CrowdedLeverageEvaluator:
    """
    Background service that evaluates CROWDED_LEVERAGE conditions every 5 minutes.

    Reads the derivatives cache (funding_zscore, oi_change_pct) for each symbol.
    Fires MEDIUM severity when both conditions are met; escalates to HIGH when
    funding_zscore exceeds the high threshold.

    Persistence: 2 cycles — sustained crowding required before alerting.
    Cooldown: 60 minutes per symbol.
    All failures are logged and swallowed — Rule 1.3 (graceful degradation).
    """

    def __init__(
        self,
        settings: Settings,
        redis: Any,
        engine: AlertEngine,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._params = CrowdedLeverageParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """Main loop: evaluate CROWDED_LEVERAGE every feature_interval_secs until shutdown."""
        log.info(
            "crowded_leverage.starting",
            interval_secs=self._settings.feature_interval_secs,
            funding_zscore_threshold=self._params.funding_zscore_threshold,
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
                        "crowded_leverage.symbol_failed",
                        symbol=symbol,
                        error=str(result),
                    )

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("crowded_leverage.stopped")

    async def _evaluate_symbol(self, symbol: str, cycle_time: datetime) -> None:
        """Evaluate CROWDED_LEVERAGE conditions for one symbol."""
        sym_lower = symbol.lower()

        raw_deriv = await self._redis.get(_KEY_DERIVATIVES.format(sym=sym_lower))

        if raw_deriv is None:
            log.debug("crowded_leverage.derivatives_miss", symbol=symbol)
            return

        deriv = json.loads(raw_deriv)["features"]

        funding_zscore = float(deriv.get("funding_zscore") or 0.0)
        oi_change_pct = float(deriv.get("oi_change_pct") or 0.0)

        conditions_met = (
            funding_zscore >= self._params.funding_zscore_threshold
            and oi_change_pct >= self._params.oi_change_pct_threshold
        )

        severity = (
            "HIGH"
            if funding_zscore >= self._params.funding_zscore_high
            else "MEDIUM"
        )

        trigger_values = {
            "funding_zscore": funding_zscore,
            "oi_change_pct": oi_change_pct,
        }

        await self._engine.evaluate_and_fire(
            alert_type=_ALERT_TYPE,
            symbol=symbol,
            direction="long" if funding_zscore > 0 else "short",
            conditions_met=conditions_met,
            severity=severity,
            trigger_values=trigger_values,
            context={
                "funding_zscore": funding_zscore,
                "oi_change_pct": oi_change_pct,
            },
            input_snapshot=deriv,
            fire_time=cycle_time,
        )
