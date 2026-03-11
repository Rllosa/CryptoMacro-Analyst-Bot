"""
DerivativesEngine — background service (FE-4).

Reads from derivatives_metrics (written by CoinglassCollector / DI-5) every
feature_interval_secs and computes 5 per-symbol features:

  - funding_rate       average funding rate across exchanges (latest snapshot)
  - funding_zscore     z-score vs 90-day history
  - oi_change_pct      % OI change vs 1h ago
  - oi_drop_1h         binary flag: 1.0 if OI fell >= 5% in the last hour
  - liquidations_1h_usd total USD liquidations in the last hour

Writes to:
  - computed_features (TimescaleDB) — via features.db.upsert_features
  - derivatives:latest:{sym}usdt (Redis, TTL 600s) — read by RegimeClassifier

Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from config import Settings
from derivatives.cache import cache_derivatives
from derivatives.config import DerivativeParams
from derivatives.db import fetch_funding_stats, fetch_latest_snapshot, fetch_oi_1h_ago
from derivatives.indicators import (
    compute_funding_zscore,
    compute_oi_change_pct,
    compute_oi_drop_1h,
)
from features.db import upsert_features
from ops.degrade import DegradePublisher, STATUS_DEGRADED, STATUS_HEALTHY

log = structlog.get_logger()

_SYMBOLS = ["BTC", "ETH", "SOL", "HYPE"]
_MAX_FAILURES = 3


class DerivativesEngine:
    """
    Background service that computes derivatives features every 5 minutes.

    Per-symbol failures are logged and skipped; remaining symbols still write.
    After _MAX_FAILURES consecutive full-cycle failures, transitions to DEGRADED
    and notifies via DegradePublisher (OPS-3).
    """

    def __init__(
        self,
        settings: Settings,
        pool: Any,
        redis: Any,
        degrade_publisher: DegradePublisher | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis
        self._params = DerivativeParams.load(settings.thresholds_path)
        self._shutdown = asyncio.Event()
        self._consecutive_failures = 0
        self._degrade_publisher = degrade_publisher

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """
        Main loop: compute derivatives features every feature_interval_secs.

        Uses elapsed-aware sleep to avoid drift across cycles.
        """
        log.info(
            "derivatives_engine.starting",
            interval_secs=self._settings.feature_interval_secs,
            symbols=_SYMBOLS,
        )
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            try:
                await self._compute_cycle(datetime.now(tz=timezone.utc))
                if self._consecutive_failures > 0:
                    if self._degrade_publisher is not None:
                        await self._degrade_publisher.report(
                            "derivatives_engine", STATUS_HEALTHY, "Derivatives computation recovered"
                        )
                self._consecutive_failures = 0
            except Exception as exc:
                self._consecutive_failures += 1
                log.error(
                    "derivatives_engine.cycle_failed",
                    error=str(exc),
                    consecutive=self._consecutive_failures,
                )
                if self._consecutive_failures >= _MAX_FAILURES and self._degrade_publisher is not None:
                    await self._degrade_publisher.report(
                        "derivatives_engine",
                        STATUS_DEGRADED,
                        f"Derivatives computation failing — {self._consecutive_failures} consecutive errors",
                    )

            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, self._settings.feature_interval_secs - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)

        log.info("derivatives_engine.stopped")

    async def _compute_cycle(self, cycle_time: datetime) -> None:
        """Process all symbols concurrently; log per-symbol failures."""
        results = await asyncio.gather(
            *(self._process_symbol(sym, cycle_time) for sym in _SYMBOLS),
            return_exceptions=True,
        )
        for sym, result in zip(_SYMBOLS, results):
            if isinstance(result, Exception):
                log.warning(
                    "derivatives_engine.symbol_failed",
                    symbol=sym,
                    error=str(result),
                )

        log.info("derivatives_engine.cycle_complete", cycle_time=cycle_time.isoformat())

    async def _process_symbol(self, symbol: str, cycle_time: datetime) -> None:
        """
        For one symbol:
          1. Run 3 DB queries concurrently.
          2. Compute indicators.
          3. Write to Redis cache and computed_features.
        """
        avg_funding, total_oi, total_liq = await fetch_latest_snapshot(self._pool, symbol)

        if avg_funding is None and total_oi is None and total_liq is None:
            log.debug("derivatives_engine.no_data", symbol=symbol)
            return

        oi_1h_ago, (mean_funding, std_funding, n_samples) = await asyncio.gather(
            fetch_oi_1h_ago(self._pool, symbol),
            fetch_funding_stats(
                self._pool, symbol, self._params.funding_zscore_lookback_days
            ),
        )

        funding_zscore = compute_funding_zscore(
            current=avg_funding if avg_funding is not None else 0.0,
            mean=mean_funding,
            std=std_funding,
            n_samples=n_samples,
            min_samples=self._params.funding_zscore_min_samples,
        )
        oi_change_pct = compute_oi_change_pct(total_oi, oi_1h_ago)
        oi_drop_1h = compute_oi_drop_1h(oi_change_pct, self._params.oi_drop_threshold_pct)

        features: dict[str, float | None] = {
            "funding_rate": avg_funding,
            "funding_zscore": funding_zscore,
            "oi_change_pct": oi_change_pct,
            "oi_drop_1h": oi_drop_1h,
            "liquidations_1h_usd": total_liq,
        }

        await cache_derivatives(self._redis, symbol, cycle_time, features)

        # Write to computed_features using the same EAV pattern as FE-1.
        # symbol column uses the USDT pair format (e.g. "BTCUSDT") matching FE-1.
        db_symbol = f"{symbol}USDT"
        rows = [
            (cycle_time, db_symbol, name, value, None)
            for name, value in features.items()
            if value is not None
        ]
        if rows:
            await upsert_features(self._pool, rows)

        log.debug(
            "derivatives_engine.symbol_done",
            symbol=symbol,
            funding_zscore=round(funding_zscore, 4),
            oi_drop_1h=oi_drop_1h,
            liquidations_1h_usd=total_liq,
        )
