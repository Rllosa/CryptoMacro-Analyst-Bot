"""
AL-12: NEWS_EVENT Alert Evaluator (SOLO-96)

Deterministic evaluator — no LLM in this path (Rule 1.1 preserved).

Reads news_signals:latest from Redis (written by LLM-2b NewsClassifier),
applies trigger rules, and fires NEWS_EVENT alerts via AlertEngine.

Trigger conditions (all must hold):
  - relevant == True
  - confidence == "high"   (min_confidence from thresholds.yaml)
  - direction NOT IN excluded_directions (default: ["neutral"])
  - age_minutes <= max_age_minutes (default: 20)

Cooldown: per (asset, event_type) pair — 60 minutes.
  dedup_key = f"{symbol}:{event_type}" passed as `direction` to AlertEngine
  so the engine's standard cooldown key (cooldown:NEWS_EVENT:{symbol}:{event_type})
  gives the correct per-pair isolation.

Severity:
  HIGH   — direction in [bullish, bearish] AND confidence == "high"
  MEDIUM — direction == "ambiguous" or any other qualifying case

Persistence: 1 cycle (news events are time-sensitive; no wait required).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
import yaml

from alerts.engine import AlertEngine
from config import Settings

log = structlog.get_logger()

_ALERT_TYPE = "NEWS_EVENT"
_REDIS_KEY = "news_signals:latest"

_HIGH_DIRECTIONS = frozenset({"bullish", "bearish"})


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsEventParams:
    """Trigger thresholds for NEWS_EVENT — loaded from thresholds.yaml."""

    min_confidence: str                 # "high"
    max_age_minutes: int                # 20
    excluded_directions: frozenset[str] # {"neutral"}

    @classmethod
    def from_thresholds(cls, thresholds: dict[str, Any]) -> NewsEventParams:
        cfg = thresholds.get("news_event", {})
        return cls(
            min_confidence=cfg.get("min_confidence", "high"),
            max_age_minutes=int(cfg.get("max_age_minutes", 20)),
            excluded_directions=frozenset(cfg.get("excluded_directions", ["neutral"])),
        )

    @classmethod
    def load(cls, thresholds_path: str) -> NewsEventParams:
        with open(thresholds_path) as fh:
            return cls.from_thresholds(yaml.safe_load(fh))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class NewsEventEvaluator:
    """
    Background service that evaluates NEWS_EVENT conditions every 5 minutes.

    Reads the news_signals:latest Redis list (written by NewsClassifier).
    Deduplicates by (asset, event_type) within a cycle — fires at most one
    alert per pair per cycle (before cooldown check).

    All failures are logged and swallowed — Rule 1.3 (graceful degradation).
    """

    def __init__(self, settings: Settings, redis: Any, engine: AlertEngine) -> None:
        self._settings = settings
        self._redis = redis
        self._engine = engine
        self._params = NewsEventParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the run loop to exit after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """Main loop: evaluate NEWS_EVENT every feature_interval_secs until shutdown."""
        log.info(
            "news_event.starting",
            interval_secs=self._settings.feature_interval_secs,
            max_age_minutes=self._params.max_age_minutes,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            cycle_time = datetime.now(tz=timezone.utc)

            try:
                await self._evaluate_cycle(cycle_time)
            except Exception as exc:
                log.warning("news_event.cycle_failed", error=str(exc))

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("news_event.stopped")

    async def _evaluate_cycle(self, cycle_time: datetime) -> None:
        """One evaluation pass — read signals, filter, dedup, fire."""
        raw_signals = await self._redis.lrange(_REDIS_KEY, 0, -1)
        if not raw_signals:
            log.debug("news_event.no_signals")
            return

        qualifying: dict[tuple[str, str], dict[str, Any]] = {}

        for raw in raw_signals:
            try:
                sig = json.loads(raw)
            except Exception:
                continue

            if not self._qualifies(sig):
                continue

            asset = _resolve_symbol(sig.get("assets") or [])
            event_type = sig.get("event_type", "other")
            key = (asset, event_type)

            # Keep the newest qualifying signal per (asset, event_type).
            # news_signals:latest is lpush'd newest-first, so the first one
            # we encounter for a key is the most recent — skip later ones.
            if key not in qualifying:
                qualifying[key] = sig

        for (asset, event_type), sig in qualifying.items():
            await self._fire(asset, event_type, sig, cycle_time)

    def _qualifies(self, sig: dict[str, Any]) -> bool:
        """Return True if a signal meets all trigger conditions."""
        if not sig.get("relevant"):
            return False
        if sig.get("confidence") != self._params.min_confidence:
            return False
        direction = sig.get("direction", "neutral")
        if direction in self._params.excluded_directions:
            return False
        if int(sig.get("age_minutes", 9999)) > self._params.max_age_minutes:
            return False
        return True

    async def _fire(
        self,
        asset: str,
        event_type: str,
        sig: dict[str, Any],
        cycle_time: datetime,
    ) -> None:
        """Call AlertEngine for one qualifying (asset, event_type) pair."""
        direction = sig.get("direction", "ambiguous")
        confidence = sig.get("confidence", "high")
        severity = "HIGH" if direction in _HIGH_DIRECTIONS and confidence == "high" else "MEDIUM"

        # Use event_type as the `direction` arg so AlertEngine's dedup key
        # becomes f"{asset}:{event_type}" — giving per-(asset, event_type) cooldown.
        await self._engine.evaluate_and_fire(
            alert_type=_ALERT_TYPE,
            symbol=asset,
            direction=event_type,
            conditions_met=True,
            severity=severity,
            trigger_values={
                "direction": direction,
                "confidence": confidence,
                "age_minutes": sig.get("age_minutes", 0),
            },
            context={
                "event_type": event_type,
                "headline": sig.get("headline", ""),
                "source": sig.get("source", ""),
                "age_minutes": sig.get("age_minutes", 0),
                "unconfirmed": True,
                "disclaimer": (
                    "Unconfirmed until structural signals follow. "
                    "Watch for VOL_EXPANSION / REGIME_SHIFT in next 1-3 cycles."
                ),
            },
            input_snapshot={
                "news_event_id": sig.get("news_event_id"),
                "assets": sig.get("assets", []),
                "reasoning": sig.get("reasoning", ""),
            },
            fire_time=cycle_time,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_symbol(assets: list[str]) -> str:
    """Return the first asset as symbol, or 'MARKET' for multi-asset/empty."""
    if len(assets) == 1:
        return assets[0]
    return "MARKET"
