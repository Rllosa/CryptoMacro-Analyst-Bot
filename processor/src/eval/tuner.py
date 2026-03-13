"""
EV-3: Threshold Tuning Framework

Standalone CLI that reads raw alert_outcomes rows from the DB, sweeps
hit_threshold_pct across candidate values, and emits a JSON report with
per-alert-type recommendations. Manual application only — no auto-tuning.

Reuses aggregate_rows() and config_hash() from eval.metrics (same service).

Usage:
    cd processor
    .venv/bin/python -m eval.tuner [--days 30] [--out report.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import structlog
import yaml
import psycopg

from eval.metrics import aggregate_rows, config_hash, _FETCH_OUTCOMES_SQL
from config import Settings

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_CANDIDATE_THRESHOLDS: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0)

_OK_HIT_RATE:     float = 0.60  # hit_rate >= this → OK
_RAISE_HIT_RATE:  float = 0.40  # hit_rate <  this → too many FPs, consider raising trigger
_LOWER_HIT_RATE:  float = 0.80  # hit_rate >  this → very tight, may be missing valid moves

# Sort priority for recommendation states (lower = higher priority in report)
_SORT_ORDER: dict[str, int] = {
    "RAISE_THRESHOLD":    0,
    "LOWER_THRESHOLD":    1,
    "INSUFFICIENT_DATA":  2,
    "OK":                 3,
}


# ---------------------------------------------------------------------------
# Recommendation engine (pure function — testable without DB)
# ---------------------------------------------------------------------------


def _classify(hit_rate: float | None, count: int, min_sample: int) -> str:
    """Classify an alert type based on its hit_rate at the current threshold."""
    if count < min_sample:
        return "INSUFFICIENT_DATA"
    if hit_rate is None:
        return "INSUFFICIENT_DATA"
    if hit_rate < _RAISE_HIT_RATE:
        return "RAISE_THRESHOLD"
    if hit_rate > _LOWER_HIT_RATE:
        return "LOWER_THRESHOLD"
    return "OK"


def build_recommendations(
    rows: list[dict],
    current_threshold: float,
    min_sample: int,
) -> list[dict]:
    """
    Sweep candidate thresholds per alert type and return sorted recommendations.

    Args:
        rows:              Raw outcome rows (same format as aggregate_rows input).
        current_threshold: The active hit_threshold_pct from thresholds.yaml.
        min_sample:        Minimum n_4h rows needed before rating a type.

    Returns:
        List of recommendation dicts, sorted by priority
        (RAISE_THRESHOLD → LOWER_THRESHOLD → INSUFFICIENT_DATA → OK).
    """
    # Group rows by alert_type (single pass)
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row["alert_type"]].append(row)

    recommendations: list[dict] = []

    for alert_type, type_rows in by_type.items():
        sweep: list[dict] = []
        current_bucket: dict = {}

        for threshold in _CANDIDATE_THRESHOLDS:
            # aggregate_rows expects a list with all alert types; filter already done
            result = aggregate_rows(type_rows, threshold, min_sample=1)
            # result["by_type"] has exactly one key (alert_type)
            bucket = result["by_type"].get(alert_type, {})
            sweep_entry = {
                "threshold":         threshold,
                "hit_rate":          bucket.get("hit_rate"),
                "fp_rate":           bucket.get("fp_rate"),
                "avg_move_4h_pct":   bucket.get("avg_move_4h_pct"),
            }
            sweep.append(sweep_entry)
            if threshold == current_threshold:
                current_bucket = bucket

        # Fall back if current_threshold not in candidates
        if not current_bucket:
            current_result = aggregate_rows(type_rows, current_threshold, min_sample=1)
            current_bucket = current_result["by_type"].get(alert_type, {})

        count = len(type_rows)
        hit_rate = current_bucket.get("hit_rate")
        recommendation = _classify(hit_rate, count, min_sample)

        recommendations.append({
            "alert_type":             alert_type,
            "count":                  count,
            "recommendation":         recommendation,
            "current_threshold":      current_threshold,
            "current_hit_rate":       hit_rate,
            "current_fp_rate":        current_bucket.get("fp_rate"),
            "current_avg_move_4h_pct": current_bucket.get("avg_move_4h_pct"),
            "sweep":                  sweep,
        })

    recommendations.sort(key=lambda r: _SORT_ORDER.get(r["recommendation"], 99))
    return recommendations


# ---------------------------------------------------------------------------
# Async DB runner
# ---------------------------------------------------------------------------


async def run_tuner(
    db_dsn: str,
    thresholds_path: str,
    symbols_path: str,
    window_days: int,
) -> dict:
    """
    Fetch outcome rows, build recommendations, return full report dict.
    """
    with Path(thresholds_path).open() as fh:
        thresholds = yaml.safe_load(fh)
    eval_params = thresholds.get("eval", {})
    current_threshold = float(eval_params.get("hit_threshold_pct", 1.0))
    min_sample = int(eval_params.get("min_sample_size", 5))

    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(days=window_days)

    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(_FETCH_OUTCOMES_SQL, (window_start, now))
            raw = await cur.fetchall()

    rows = [
        {
            "alert_type":        r[0],
            "severity":          r[1],
            "regime_at_trigger": r[2],
            "move_4h_pct":       r[3],
            "move_12h_pct":      r[4],
            "has_4h":            r[5],
            "has_12h":           r[6],
        }
        for r in raw
    ]

    recommendations = build_recommendations(rows, current_threshold, min_sample)

    return {
        "generated_at":     now.isoformat(),
        "window_days":      window_days,
        "config_hash":      config_hash(thresholds_path, symbols_path),
        "current_threshold": current_threshold,
        "total_alerts":     len(rows),
        "recommendations":  recommendations,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EV-3: Generate threshold tuning recommendations from alert outcomes."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback window in days (default: 30)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Write JSON report to this file path (in addition to stdout)",
    )
    args = parser.parse_args()

    settings = Settings()
    report = asyncio.run(
        run_tuner(
            settings.db_dsn,
            settings.thresholds_path,
            settings.symbols_path,
            args.days,
        )
    )

    output = json.dumps(report, indent=2)
    print(output)

    if args.out:
        Path(args.out).write_text(output)
        log.info("ev3_tuner.report_written", path=args.out)


if __name__ == "__main__":
    main()
