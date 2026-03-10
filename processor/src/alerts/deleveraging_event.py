"""
AL-8: DELEVERAGING_EVENT Alert Evaluator (SOLO-51)

Deterministic evaluator — no LLM in the trigger path (Rule 1.1 preserved).

Trigger conditions (all must hold, all thresholds from thresholds.yaml):
  - liquidations_1h_usd >= liq_1h_usd_threshold (default: $50M)
  - oi_drop_1h         >= oi_drop_threshold      (default: 1.0 — binary flag for ≥5% OI drop)
  - atr_ratio          >= atr_ratio_threshold     (default: 2.0 — candle ≥ 2× ATR)

Severity: always HIGH (cascade liquidation events are always high-impact).
Cooldown: 30 minutes per symbol.
Persistence: 1 cycle — cascades are time-sensitive; no wait.

When the alert fires, the evaluator spawns an asyncio background task to call
EventAnalyzer.analyze() (LLM-4 / SOLO-58). The alert itself is deterministic and
is not blocked by LLM availability (Rule 1.2 preserved).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from alerts.engine import AlertEngine
from backfill import SYMBOLS
from config import Settings

if TYPE_CHECKING:
    from llm.event_analyzer import EventAnalyzer

log = structlog.get_logger()

_ALERT_TYPE = "DELEVERAGING_EVENT"

# Redis key templates — module-level, not rebuilt per call
_KEY_DERIVATIVES = "derivatives:latest:{sym}"
_KEY_FEATURES = "features:latest:{sym}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeleveragingParams:
    """Trigger thresholds for DELEVERAGING_EVENT — loaded from thresholds.yaml."""

    liq_1h_usd_threshold: float   # $50M
    oi_drop_threshold: float      # 1.0 (binary flag: OI dropped >= 5%)
    atr_ratio_threshold: float    # 2.0 (candle >= 2× ATR)

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> DeleveragingParams:
        cfg = thresholds.get("deleveraging_event", {})
        return cls(
            liq_1h_usd_threshold=float(cfg.get("liq_1h_usd_threshold", 50_000_000)),
            oi_drop_threshold=float(cfg.get("oi_drop_threshold", 1.0)),
            atr_ratio_threshold=float(cfg.get("atr_ratio_threshold", 2.0)),
        )

    @classmethod
    def load(cls, thresholds_path: str) -> DeleveragingParams:
        with open(thresholds_path) as fh:
            return cls.from_thresholds(yaml.safe_load(fh))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class DeleveragingEvaluator:
    """
    Background service that evaluates DELEVERAGING_EVENT conditions every 5 minutes.

    Reads both the derivatives cache (liquidations_1h_usd, oi_drop_1h) and the
    feature cache (atr_ratio) concurrently for each symbol. Fires always at HIGH
    severity when all three conditions are met.

    If an EventAnalyzer is provided, spawns an async background task (fire-and-forget)
    to generate LLM-4 event analysis after the alert fires. The alert itself is never
    blocked by LLM availability — Rule 1.2 preserved.

    All failures are logged and swallowed — Rule 1.3 (graceful degradation).
    """

    def __init__(
        self,
        settings: Settings,
        redis: Any,
        engine: AlertEngine,
        event_analyzer: EventAnalyzer | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._event_analyzer = event_analyzer
        self._params = DeleveragingParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """Main loop: evaluate DELEVERAGING_EVENT every feature_interval_secs until shutdown."""
        log.info(
            "deleveraging_event.starting",
            interval_secs=self._settings.feature_interval_secs,
            liq_threshold_usd=self._params.liq_1h_usd_threshold,
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
                        "deleveraging_event.symbol_failed",
                        symbol=symbol,
                        error=str(result),
                    )

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("deleveraging_event.stopped")

    async def _evaluate_symbol(self, symbol: str, cycle_time: datetime) -> None:
        """Evaluate DELEVERAGING_EVENT conditions for one symbol."""
        sym_lower = symbol.lower()

        # Read derivatives and feature caches concurrently
        raw_deriv, raw_feat = await asyncio.gather(
            self._redis.get(_KEY_DERIVATIVES.format(sym=sym_lower)),
            self._redis.get(_KEY_FEATURES.format(sym=sym_lower)),
        )

        if raw_deriv is None:
            log.debug("deleveraging_event.derivatives_miss", symbol=symbol)
            return
        if raw_feat is None:
            log.debug("deleveraging_event.features_miss", symbol=symbol)
            return

        deriv = json.loads(raw_deriv)["features"]
        feat = json.loads(raw_feat)["features"]

        liq = float(deriv.get("liquidations_1h_usd") or 0.0)
        oi_drop = float(deriv.get("oi_drop_1h") or 0.0)
        atr_ratio = float(feat.get("atr_ratio") or 0.0)

        conditions_met = (
            liq >= self._params.liq_1h_usd_threshold
            and oi_drop >= self._params.oi_drop_threshold
            and atr_ratio >= self._params.atr_ratio_threshold
        )

        trigger_values = {
            "liquidations_1h_usd": liq,
            "oi_drop_1h": oi_drop,
            "atr_ratio": atr_ratio,
        }

        fired = await self._engine.evaluate_and_fire(
            alert_type=_ALERT_TYPE,
            symbol=symbol,
            direction="cascade",
            conditions_met=conditions_met,
            severity="HIGH",
            trigger_values=trigger_values,
            context={
                "liq_1h_usd": liq,
                "oi_drop_1h": oi_drop,
                "atr_ratio": atr_ratio,
            },
            input_snapshot={**deriv, **feat},
            fire_time=cycle_time,
        )

        if fired and self._event_analyzer is not None:
            asyncio.create_task(
                self._event_analyzer.analyze(
                    alert_type=_ALERT_TYPE,
                    symbol=symbol,
                    severity="HIGH",
                    fire_time=cycle_time,
                    trigger_values=trigger_values,
                )
            )
