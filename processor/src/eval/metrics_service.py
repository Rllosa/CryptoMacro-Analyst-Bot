"""
EV-2: Metrics Service

Background service that runs every hour. Computes alert quality metrics for 7d
and 30d windows and writes them to Redis for the /api/eval/metrics endpoint.

On Sundays, writes a weekly quality summary to analysis_reports (no LLM —
pure deterministic aggregation per Rule 1.2).

Lifecycle: background service with run() loop — added to asyncio.gather in main.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

from eval.metrics import compute_metrics

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_CYCLE_INTERVAL_SECS = 3600  # 1 hour
_WINDOWS = (7, 30)

_REDIS_KEY_7D = "eval:metrics:7d"
_REDIS_KEY_30D = "eval:metrics:30d"

_WEEKLY_GUARD_TMPL = "eval:weekly_written:{iso_week}"
_WEEKLY_GUARD_TTL = 691200   # 8 days
_WEEKLY_REPORT_TYPE = "weekly_quality_summary"

# SQL for weekly summary INSERT — hoisted at module level
_INSERT_WEEKLY_SQL = (
    "INSERT INTO analysis_reports "
    "(report_type, title, content, regime_context, model_used, metadata) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)


class MetricsService:
    """
    Hourly alert quality metrics computation.

    Computes 7d and 30d hit rates, avg moves, FP rates grouped by alert type,
    severity, and regime. Caches results in Redis. Writes weekly summary on Sundays.
    Cycle-level failures are logged in run() and do not crash the service.
    """

    def __init__(self, settings: Any, pool: Any, redis_client: Any) -> None:
        self._settings = settings
        self._pool = pool
        self._redis = redis_client
        self._shutdown = asyncio.Event()
        self._params = self._load_eval_params()

    def _load_eval_params(self) -> dict:
        """Load eval section from thresholds.yaml."""
        with Path(self._settings.thresholds_path).open() as fh:
            thresholds = yaml.safe_load(fh)
        return thresholds.get("eval", {})

    def request_shutdown(self) -> None:
        """Signal the run loop to stop after the current cycle completes."""
        self._shutdown.set()

    async def run(self) -> None:
        """Main loop: compute and cache quality metrics every hour."""
        log.info("metrics_service.starting", interval_secs=_CYCLE_INTERVAL_SECS)
        while not self._shutdown.is_set():
            cycle_start = time.monotonic()
            try:
                await self._run_cycle(datetime.now(tz=timezone.utc))
            except Exception as exc:
                log.error("metrics_service.cycle_failed", error=str(exc))
            elapsed = time.monotonic() - cycle_start
            sleep_secs = max(0.0, _CYCLE_INTERVAL_SECS - elapsed)
            if sleep_secs > 0:
                await asyncio.sleep(sleep_secs)
        log.info("metrics_service.stopped")

    async def _run_cycle(self, now: datetime) -> None:
        """
        Compute 7d and 30d metrics concurrently, write to Redis,
        and conditionally write the weekly summary to analysis_reports.
        """
        hit_threshold = float(self._params.get("hit_threshold_pct", 1.0))
        min_sample = int(self._params.get("min_sample_size", 5))
        redis_ttl = int(self._params.get("redis_ttl_secs", 7200))
        weekly_day = int(self._params.get("weekly_summary_day", 6))

        # Compute both windows concurrently
        results = await asyncio.gather(
            compute_metrics(
                self._pool, now, 7, hit_threshold, min_sample,
                self._settings.thresholds_path, self._settings.symbols_path,
            ),
            compute_metrics(
                self._pool, now, 30, hit_threshold, min_sample,
                self._settings.thresholds_path, self._settings.symbols_path,
            ),
            return_exceptions=True,
        )

        result_7d, result_30d = results[0], results[1]

        # Write 7d to Redis
        if isinstance(result_7d, Exception):
            log.warning("metrics_service.7d_compute_failed", error=str(result_7d))
        else:
            await self._redis.set(_REDIS_KEY_7D, json.dumps(result_7d), ex=redis_ttl)
            log.info(
                "metrics_service.7d_cached",
                total_alerts=result_7d["total_alerts"],
                config_hash=result_7d["config_hash"],
            )

        # Write 30d to Redis
        if isinstance(result_30d, Exception):
            log.warning("metrics_service.30d_compute_failed", error=str(result_30d))
        else:
            await self._redis.set(_REDIS_KEY_30D, json.dumps(result_30d), ex=redis_ttl)
            log.info(
                "metrics_service.30d_cached",
                total_alerts=result_30d["total_alerts"],
                config_hash=result_30d["config_hash"],
            )

        # Weekly summary on the configured day (default Sunday = 6)
        if now.weekday() == weekly_day and not isinstance(result_30d, Exception):
            await self._maybe_write_weekly_summary(now, result_30d)

    async def _maybe_write_weekly_summary(self, now: datetime, metrics_30d: dict) -> None:
        """Write weekly summary to analysis_reports if not already written this week."""
        iso_week = now.strftime("%Y-W%W")
        guard_key = _WEEKLY_GUARD_TMPL.format(iso_week=iso_week)

        already_written = await self._redis.get(guard_key)
        if already_written:
            return

        title = f"Weekly Alert Quality Report — {iso_week}"
        content = json.dumps(metrics_30d)
        metadata = json.dumps({
            "window_days": 30,
            "total_alerts": metrics_30d["total_alerts"],
            "config_hash": metrics_30d["config_hash"],
        })

        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    _INSERT_WEEKLY_SQL,
                    (
                        _WEEKLY_REPORT_TYPE,
                        title,
                        content,
                        "{}",   # regime_context — not applicable for quality reports
                        "none", # model_used — no LLM
                        metadata,
                    ),
                )
            # Guard key prevents double-write within the same week
            await self._redis.set(guard_key, "1", ex=_WEEKLY_GUARD_TTL)
            log.info("metrics_service.weekly_summary_written", iso_week=iso_week)
        except Exception as exc:
            log.warning("metrics_service.weekly_summary_failed", error=str(exc))
