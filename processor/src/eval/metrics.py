"""
EV-2: Alert Quality Metrics computation.

Reads alert_outcomes joined with alerts (for regime_at_trigger) and aggregates
hit rate, FP rate, avg move, and coverage by alert type, severity, and regime.

All SQL strings are hoisted at module level — never rebuilt per call.
Aggregation is pure Python (single pass over rows) for testability.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# SQL — hoisted at module level
# ---------------------------------------------------------------------------

_FETCH_OUTCOMES_SQL = """
    SELECT
        ao.alert_type,
        ao.severity,
        a.regime_at_trigger,
        ao.move_4h_pct,
        ao.move_12h_pct,
        (ao.price_4h  IS NOT NULL) AS has_4h,
        (ao.price_12h IS NOT NULL) AS has_12h
    FROM alert_outcomes ao
    JOIN alerts a
      ON ao.alert_id  = a.id
     AND a.time       = ao.alert_fired_at
    WHERE ao.alert_fired_at >= %s
      AND ao.alert_fired_at <  %s
"""


# ---------------------------------------------------------------------------
# Config hash
# ---------------------------------------------------------------------------


def config_hash(thresholds_path: str, symbols_path: str) -> str:
    """SHA-256 of thresholds + symbols YAML files, truncated to 12 hex chars.

    Used to correlate metrics snapshots with the config version that produced them,
    enabling before/after comparison when thresholds change (EV-3).
    """
    h = hashlib.sha256()
    h.update(Path(thresholds_path).read_bytes())
    h.update(Path(symbols_path).read_bytes())
    return h.hexdigest()[:12]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _make_bucket() -> dict:
    """Empty accumulator for a metrics bucket."""
    return {
        "count": 0,
        "hits": 0,       # |move_4h_pct| >= hit_threshold and has_4h
        "fps": 0,        # |move_4h_pct| <  hit_threshold and has_4h
        "sum_4h": 0.0,
        "sum_12h": 0.0,
        "n_4h": 0,       # rows with price_4h data
        "n_12h": 0,      # rows with price_12h data
    }


def _finalize(bucket: dict, min_sample: int) -> dict:
    """Convert raw accumulator into final metrics dict."""
    count = bucket["count"]
    n_4h = bucket["n_4h"]
    n_12h = bucket["n_12h"]

    coverage_4h = n_4h / count if count else 0.0
    coverage_12h = n_12h / count if count else 0.0

    # Only report hit/fp rates when sample is large enough to be meaningful
    if n_4h >= min_sample:
        hit_rate = round(bucket["hits"] / n_4h, 4)
        fp_rate = round(1.0 - hit_rate, 4)
    else:
        hit_rate = None
        fp_rate = None

    avg_move_4h = round(bucket["sum_4h"] / n_4h, 4) if n_4h else None
    avg_move_12h = round(bucket["sum_12h"] / n_12h, 4) if n_12h else None

    return {
        "count": count,
        "coverage_4h": round(coverage_4h, 4),
        "coverage_12h": round(coverage_12h, 4),
        "hit_rate": hit_rate,
        "fp_rate": fp_rate,
        "avg_move_4h_pct": avg_move_4h,
        "avg_move_12h_pct": avg_move_12h,
    }


def aggregate_rows(
    rows: list[dict],
    hit_threshold: float,
    min_sample: int,
) -> dict:
    """
    Single-pass aggregation over outcome rows.

    Returns:
        {
            "total_alerts": int,
            "by_type":     { alert_type: bucket },
            "by_severity": { severity: bucket },
            "by_regime":   { alert_type: { regime: bucket } },
        }
    """
    by_type: dict[str, dict] = defaultdict(lambda: _make_bucket())
    by_sev: dict[str, dict] = defaultdict(lambda: _make_bucket())
    # by_regime[alert_type][regime] = bucket
    by_regime: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: _make_bucket())
    )

    for row in rows:
        alert_type = row["alert_type"]
        severity = row["severity"]
        regime = row["regime_at_trigger"] or "UNKNOWN"
        has_4h = bool(row["has_4h"])
        has_12h = bool(row["has_12h"])
        move_4h = float(row["move_4h_pct"]) if row["move_4h_pct"] is not None else None
        move_12h = float(row["move_12h_pct"]) if row["move_12h_pct"] is not None else None

        for bucket in (by_type[alert_type], by_sev[severity], by_regime[alert_type][regime]):
            bucket["count"] += 1
            if has_4h and move_4h is not None:
                bucket["n_4h"] += 1
                bucket["sum_4h"] += move_4h
                if abs(move_4h) >= hit_threshold:
                    bucket["hits"] += 1
                else:
                    bucket["fps"] += 1
            if has_12h and move_12h is not None:
                bucket["n_12h"] += 1
                bucket["sum_12h"] += move_12h

    return {
        "total_alerts": len(rows),
        "by_type": {k: _finalize(v, min_sample) for k, v in by_type.items()},
        "by_severity": {k: _finalize(v, min_sample) for k, v in by_sev.items()},
        "by_regime": {
            atype: {regime: _finalize(b, min_sample) for regime, b in regimes.items()}
            for atype, regimes in by_regime.items()
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_metrics(
    pool: Any,
    now: datetime,
    window_days: int,
    hit_threshold_pct: float,
    min_sample_size: int,
    thresholds_path: str,
    symbols_path: str,
) -> dict:
    """
    Fetch outcome rows for the given window and aggregate quality metrics.

    Returns the full metrics payload ready to serialize to Redis.
    """
    window_start = now - timedelta(days=window_days)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_FETCH_OUTCOMES_SQL, (window_start, now))
            raw = await cur.fetchall()

    rows = [
        {
            "alert_type": r[0],
            "severity": r[1],
            "regime_at_trigger": r[2],
            "move_4h_pct": r[3],
            "move_12h_pct": r[4],
            "has_4h": r[5],
            "has_12h": r[6],
        }
        for r in raw
    ]

    aggregated = aggregate_rows(rows, hit_threshold_pct, min_sample_size)

    return {
        "computed_at": now.isoformat(),
        "config_hash": config_hash(thresholds_path, symbols_path),
        "window_days": window_days,
        **aggregated,
    }
