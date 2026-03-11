"""
EV-1: Alert Move Tracker

Background service that runs every 5 minutes. For each alert that just
crossed the 4h or 12h mark, fetches the nearest 1h close from candles_1h
and writes the outcome to alert_outcomes.

Two-pass cycle:
  Pass 1 (T+4h): INSERT row with price_at_alert + price_4h + move_4h_pct
  Pass 2 (T+12h): UPDATE existing row with price_12h + move_12h_pct

Cross-asset alerts (REGIME_SHIFT, CORRELATION_BREAK) have NULL symbol in
alerts. BTCUSDT is used as the price proxy for these — documented here and
in the DB row's symbol column.

Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from eval.db import (
    fetch_alerts_for_4h_tracking,
    fetch_alerts_for_12h_tracking,
    fetch_price_near,
    upsert_4h_outcome,
    update_12h_outcome,
)

log = structlog.get_logger()

# BTC proxy for cross-asset alerts that have NULL symbol
_PROXY_SYMBOL = "BTCUSDT"

# Cycle interval — matches feature engine cadence
_CYCLE_INTERVAL_SECS = 300


class AlertMoveTracker:
    """
    Tracks post-alert price moves at 4h and 12h windows for EV-1.

    Per-alert failures are logged and skipped; remaining alerts still process.
    Cycle-level failures are logged in run() and do not crash the service.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """Main loop: track post-alert moves every 5 minutes."""
        log.info("alert_move_tracker.starting", interval_secs=_CYCLE_INTERVAL_SECS)
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            try:
                await self._run_cycle(datetime.now(tz=timezone.utc))
            except Exception as exc:
                log.error("alert_move_tracker.cycle_failed", error=str(exc))
            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, _CYCLE_INTERVAL_SECS - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)
        log.info("alert_move_tracker.stopped")

    async def _run_cycle(self, now: datetime) -> None:
        """
        4h pass: find newly-crossed alerts, insert outcome rows.
        12h pass: find outcome rows missing 12h data, fill them in.
        Both passes use gather so individual alert failures don't block others.
        """
        alerts_4h, alerts_12h = await asyncio.gather(
            fetch_alerts_for_4h_tracking(self._pool, now),
            fetch_alerts_for_12h_tracking(self._pool, now),
        )

        results_4h = await asyncio.gather(
            *(self._record_4h(alert, now) for alert in alerts_4h),
            return_exceptions=True,
        )
        for alert, result in zip(alerts_4h, results_4h):
            if isinstance(result, Exception):
                log.warning(
                    "alert_move_tracker.4h_record_failed",
                    alert_id=str(alert["id"]),
                    error=str(result),
                )

        results_12h = await asyncio.gather(
            *(self._record_12h(alert) for alert in alerts_12h),
            return_exceptions=True,
        )
        for alert, result in zip(alerts_12h, results_12h):
            if isinstance(result, Exception):
                log.warning(
                    "alert_move_tracker.12h_record_failed",
                    alert_id=str(alert["alert_id"]),
                    error=str(result),
                )

        log.info(
            "alert_move_tracker.cycle_done",
            tracked_4h=len(alerts_4h),
            tracked_12h=len(alerts_12h),
        )

    async def _record_4h(self, alert: dict, now: datetime) -> None:
        """
        Fetch price_at_alert and price_4h concurrently; insert outcome row.
        Skips if either candle is missing (data gap — retried next cycle).
        """
        symbol = alert["symbol"] or _PROXY_SYMBOL
        fired_at: datetime = alert["time"]

        price_at_alert, price_4h = await asyncio.gather(
            fetch_price_near(self._pool, symbol, fired_at),
            fetch_price_near(self._pool, symbol, fired_at + timedelta(hours=4)),
        )

        if price_at_alert is None or price_4h is None:
            log.debug(
                "alert_move_tracker.4h_skip_no_candle",
                alert_id=str(alert["id"]),
                symbol=symbol,
                missing_at=("alert_time" if price_at_alert is None else "4h"),
            )
            return

        move_4h_pct = round((price_4h - price_at_alert) / price_at_alert * 100, 4)

        await upsert_4h_outcome(
            self._pool,
            alert_id=alert["id"],
            alert_fired_at=fired_at,
            symbol=symbol,
            alert_type=alert["alert_type"],
            severity=alert["severity"],
            price_at_alert=price_at_alert,
            price_4h=price_4h,
            move_4h_pct=move_4h_pct,
        )
        log.debug(
            "alert_move_tracker.4h_recorded",
            alert_id=str(alert["id"]),
            symbol=symbol,
            move_4h_pct=move_4h_pct,
        )

    async def _record_12h(self, alert: dict) -> None:
        """
        Fetch price_12h; update existing outcome row.
        Skips if candle is missing (retried next cycle).
        """
        symbol: str = alert["symbol"]
        fired_at: datetime = alert["alert_fired_at"]
        price_at_alert = float(alert["price_at_alert"])

        price_12h = await fetch_price_near(
            self._pool, symbol, fired_at + timedelta(hours=12)
        )

        if price_12h is None:
            log.debug(
                "alert_move_tracker.12h_skip_no_candle",
                alert_id=str(alert["alert_id"]),
                symbol=symbol,
            )
            return

        move_12h_pct = round((price_12h - price_at_alert) / price_at_alert * 100, 4)

        await update_12h_outcome(
            self._pool,
            alert_id=alert["alert_id"],
            price_12h=price_12h,
            move_12h_pct=move_12h_pct,
        )
        log.debug(
            "alert_move_tracker.12h_recorded",
            alert_id=str(alert["alert_id"]),
            symbol=symbol,
            move_12h_pct=move_12h_pct,
        )
