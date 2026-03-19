"""
EV-4: Backtesting Framework

CLI that replays historical computed_features + cross_features through alert
trigger logic with in-memory cooldown/persistence simulation.

No side effects — no writes to Redis, NATS, or DB.

Supported alert types: VOL_EXPANSION, BREAKOUT, LEADERSHIP_ROTATION.

For each simulated fire, fetches actual 4h/12h price moves from candles_1h
and aggregates quality metrics using eval.metrics.aggregate_rows (EV-2).

Usage:
    cd processor
    .venv/bin/python -m eval.backtest [--days 30] [--out report.json] [--csv out.csv]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import structlog
import yaml

from alerts.breakout import BreakoutParams
from alerts.leadership_rotation import LeadershipRotationParams
from alerts.symbol_multipliers import SymbolMultipliers
from alerts.vol_expansion import _compute_rv_zscore, _RV_BUFFER_SIZE
from backfill import SYMBOLS
from config import Settings
from eval.metrics import aggregate_rows, config_hash

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROXY_SYMBOL = "BTCUSDT"          # Price proxy for cross-asset alerts (no symbol)
_PRICE_TOLERANCE = timedelta(minutes=90)
_SUPPORTED_TYPES = ("VOL_EXPANSION", "BREAKOUT", "LEADERSHIP_ROTATION")

# Mirrors _PAIRS in alerts/leadership_rotation.py — hoisted at module level
_LR_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("eth_btc_rs", "eth_btc_rs_zscore", "eth"),
    ("sol_btc_rs", "sol_btc_rs_zscore", "sol"),
    ("hype_btc_rs", "hype_btc_rs_zscore", "hype"),
)

# Mirrors _DIRECTIONS in alerts/breakout.py — hoisted at module level
_BO_DIRECTIONS: tuple[tuple[str, str, str | None, bool], ...] = (
    ("high_24h", "breakout_24h_high", None, True),
    ("high_4h",  "breakout_4h_high",  "breakout_24h_high", False),
    ("low_24h",  "breakout_24h_low",  None, True),
    ("low_4h",   "breakout_4h_low",   "breakout_24h_low", False),
)

# ---------------------------------------------------------------------------
# SQL — hoisted at module level
# ---------------------------------------------------------------------------

_FETCH_COMPUTED_SQL = """
    SELECT time, symbol, feature_name, value
    FROM computed_features
    WHERE time >= %s AND time < %s
    ORDER BY time ASC, symbol, feature_name
"""

_FETCH_CROSS_SQL = """
    SELECT time, feature_name, value
    FROM cross_features
    WHERE time >= %s AND time < %s
    ORDER BY time ASC, feature_name
"""

_FETCH_CANDLES_SQL = """
    SELECT symbol, bucket, close
    FROM candles_1h
    WHERE symbol = ANY(%s)
      AND bucket >= %s
      AND bucket <  %s
    ORDER BY symbol, bucket ASC
"""

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SimulatedAlert:
    fire_time: datetime
    alert_type: str
    symbol: str | None      # None for cross-asset alerts
    direction: str
    severity: str
    move_4h_pct: float | None = field(default=None)
    move_12h_pct: float | None = field(default=None)


# ---------------------------------------------------------------------------
# In-memory cooldown + persistence (no Redis)
# ---------------------------------------------------------------------------


class _InMemoryCooldowns:
    """Dict-backed cooldown registry — replaces CooldownRegistry for backtest."""

    def __init__(self) -> None:
        self._expires: dict[str, datetime] = {}

    def is_active(self, alert_type: str, dedup_key: str, fire_time: datetime) -> bool:
        expires = self._expires.get(f"{alert_type}:{dedup_key}")
        return expires is not None and fire_time < expires

    def activate(self, alert_type: str, dedup_key: str, fire_time: datetime, minutes: int) -> None:
        self._expires[f"{alert_type}:{dedup_key}"] = fire_time + timedelta(minutes=minutes)

    def reset(self, alert_type: str, dedup_key: str) -> None:
        self._expires.pop(f"{alert_type}:{dedup_key}", None)


class _InMemoryPersistence:
    """Dict-backed persistence counter — replaces PersistenceTracker for backtest."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def record_met(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def record_not_met(self, key: str) -> None:
        self._counts[key] = 0


# ---------------------------------------------------------------------------
# Core signal evaluator (mirrors AlertEngine.evaluate_and_fire — no I/O)
# ---------------------------------------------------------------------------


def _evaluate_signal(
    *,
    alert_type: str,
    symbol: str | None,
    direction: str,
    conditions_met: bool,
    severity: str,
    fire_time: datetime,
    cooldowns: _InMemoryCooldowns,
    persistence: _InMemoryPersistence,
    cooldown_minutes: dict[str, int],
    persistence_required: dict[str, int],
) -> SimulatedAlert | None:
    """
    Mirror AlertEngine.evaluate_and_fire() without Redis/NATS/DB.
    Returns SimulatedAlert if conditions cross the fire threshold, else None.
    """
    dedup_key = f"{symbol or '_'}:{direction}"
    pkey = f"{alert_type}:{dedup_key}"

    if not conditions_met:
        persistence.record_not_met(pkey)
        cooldowns.reset(alert_type, dedup_key)
        return None

    if cooldowns.is_active(alert_type, dedup_key, fire_time):
        persistence.record_not_met(pkey)
        return None

    count = persistence.record_met(pkey)
    if count < persistence_required.get(alert_type, 1):
        return None

    cooldowns.activate(alert_type, dedup_key, fire_time, cooldown_minutes.get(alert_type, 60))
    persistence.record_not_met(pkey)
    return SimulatedAlert(
        fire_time=fire_time,
        alert_type=alert_type,
        symbol=symbol,
        direction=direction,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# Per-alert-type signal helpers
# ---------------------------------------------------------------------------


def _vol_expansion_signals(
    features: dict[str, float],
    rv_1h_zscore: float | None,
    params: object,
    multiplier: float,
    fire_time: datetime,
    cooldowns: _InMemoryCooldowns,
    persistence: _InMemoryPersistence,
    cooldown_minutes: dict[str, int],
    persistence_required: dict[str, int],
    symbol: str,
) -> list[SimulatedAlert]:
    vol_z = features.get("volume_zscore")
    if rv_1h_zscore is None or vol_z is None:
        return []

    results = []
    for direction, flag_keys, breakout_24h_key in (
        ("up",   ("breakout_4h_high", "breakout_24h_high"), "breakout_24h_high"),
        ("down", ("breakout_4h_low",  "breakout_24h_low"),  "breakout_24h_low"),
    ):
        breakout_any = any(bool(features.get(k)) for k in flag_keys)
        is_24h = bool(features.get(breakout_24h_key))
        cond = (
            rv_1h_zscore >= params.rv_1h_zscore_threshold * multiplier  # type: ignore[union-attr]
            and vol_z >= params.volume_zscore_threshold * multiplier  # type: ignore[union-attr]
            and breakout_any
        )
        severity = "MEDIUM"
        if cond and (
            rv_1h_zscore >= params.high_rv_1h_zscore * multiplier  # type: ignore[union-attr]
            and vol_z >= params.high_volume_zscore * multiplier  # type: ignore[union-attr]
            and is_24h
        ):
            severity = "HIGH"
        alert = _evaluate_signal(
            alert_type="VOL_EXPANSION", symbol=symbol, direction=direction,
            conditions_met=cond, severity=severity, fire_time=fire_time,
            cooldowns=cooldowns, persistence=persistence,
            cooldown_minutes=cooldown_minutes, persistence_required=persistence_required,
        )
        if alert:
            results.append(alert)
    return results


def _breakout_signals(
    features: dict[str, float],
    params: object,
    multiplier: float,
    fire_time: datetime,
    cooldowns: _InMemoryCooldowns,
    persistence: _InMemoryPersistence,
    cooldown_minutes: dict[str, int],
    persistence_required: dict[str, int],
    symbol: str,
) -> list[SimulatedAlert]:
    vol_z = features.get("volume_zscore", 0.0)
    volume_ok = vol_z >= params.volume_zscore_min * multiplier  # type: ignore[union-attr]

    results = []
    for direction, flag_key, exclude_key, is_24h in _BO_DIRECTIONS:
        excluded = bool(exclude_key and features.get(exclude_key))
        cond = not excluded and bool(features.get(flag_key)) and volume_ok
        severity = params.severity_24h if is_24h else params.severity_4h  # type: ignore[union-attr]
        alert = _evaluate_signal(
            alert_type="BREAKOUT", symbol=symbol, direction=direction,
            conditions_met=cond, severity=severity, fire_time=fire_time,
            cooldowns=cooldowns, persistence=persistence,
            cooldown_minutes=cooldown_minutes, persistence_required=persistence_required,
        )
        if alert:
            results.append(alert)
    return results


def _leadership_rotation_signals(
    cross: dict[str, float],
    params: object,
    fire_time: datetime,
    cooldowns: _InMemoryCooldowns,
    persistence: _InMemoryPersistence,
    cooldown_minutes: dict[str, int],
    persistence_required: dict[str, int],
) -> list[SimulatedAlert]:
    threshold = params.rs_zscore_threshold  # type: ignore[union-attr]
    results = []
    for _rs_key, zscore_key, alt in _LR_PAIRS:
        z = cross.get(zscore_key)
        if z is None:
            continue
        for direction, conditions_met in (
            (f"{alt}_over_btc", z >= threshold),
            (f"btc_over_{alt}", z <= -threshold),
        ):
            alert = _evaluate_signal(
                alert_type="LEADERSHIP_ROTATION", symbol=None, direction=direction,
                conditions_met=conditions_met, severity="MEDIUM", fire_time=fire_time,
                cooldowns=cooldowns, persistence=persistence,
                cooldown_minutes=cooldown_minutes, persistence_required=persistence_required,
            )
            if alert:
                results.append(alert)
    return results


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _fetch_features(
    conn: psycopg.AsyncConnection,
    start: datetime,
    end: datetime,
) -> tuple[dict[tuple[datetime, str], dict[str, float]], dict[datetime, dict[str, float]]]:
    """
    Batch-fetch computed_features and cross_features for the date range.

    Returns:
        computed: {(time, symbol): {feature_name: value}}
        cross:    {time: {feature_name: value}}
    """
    async with conn.cursor() as cur:
        await cur.execute(_FETCH_COMPUTED_SQL, (start, end))
        computed_rows = await cur.fetchall()

    async with conn.cursor() as cur:
        await cur.execute(_FETCH_CROSS_SQL, (start, end))
        cross_rows = await cur.fetchall()

    computed: dict[tuple[datetime, str], dict[str, float]] = defaultdict(dict)
    for t, symbol, fname, val in computed_rows:
        computed[(t, symbol)][fname] = float(val)

    cross: dict[datetime, dict[str, float]] = defaultdict(dict)
    for t, fname, val in cross_rows:
        cross[t][fname] = float(val)

    return dict(computed), dict(cross)


async def _fetch_candle_cache(
    conn: psycopg.AsyncConnection,
    symbols: list[str],
    start: datetime,
    end: datetime,
) -> dict[tuple[str, datetime], float]:
    """
    Batch-fetch all 1h candles for symbols over the range.
    Returns {(symbol, bucket): close} for O(1) move lookups.
    end should include +12h buffer so T+12h lookups are covered.
    """
    async with conn.cursor() as cur:
        await cur.execute(_FETCH_CANDLES_SQL, (symbols, start, end))
        rows = await cur.fetchall()
    return {(symbol, bucket): float(close) for symbol, bucket, close in rows}


def _price_near(
    cache: dict[tuple[str, datetime], float],
    symbol: str,
    target: datetime,
) -> float | None:
    """Look up the nearest 1h candle within ±90 min from the in-memory cache."""
    # Search backwards from +90min to -90min in 1h steps
    for delta_hours in range(0, 2):
        for sign in (0, 1, -1):
            bucket = target.replace(minute=0, second=0, microsecond=0) + timedelta(hours=sign * delta_hours)
            if (symbol, bucket) in cache:
                if abs((bucket - target).total_seconds()) <= _PRICE_TOLERANCE.total_seconds():
                    return cache[(symbol, bucket)]
    return None


# ---------------------------------------------------------------------------
# rv_1h_zscore pre-computation (avoids live evaluator's in-memory deque warmup)
# ---------------------------------------------------------------------------


def _build_rv_zscores(
    computed: dict[tuple[datetime, str], dict[str, float]],
) -> dict[tuple[datetime, str], float | None]:
    """
    Pre-compute rv_1h_zscore for every (time, symbol) using the same rolling-deque
    approach as VolExpansionEvaluator: score BEFORE appending (current excluded from distribution).
    """
    by_symbol: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for (t, symbol), features in computed.items():
        rv = features.get("rv_1h")
        if rv is not None:
            by_symbol[symbol].append((t, rv))

    for symbol in by_symbol:
        by_symbol[symbol].sort(key=lambda x: x[0])

    zscores: dict[tuple[datetime, str], float | None] = {}
    for symbol, time_vals in by_symbol.items():
        buf: deque[float] = deque(maxlen=_RV_BUFFER_SIZE)
        for t, rv in time_vals:
            zscores[(t, symbol)] = _compute_rv_zscore(buf, rv)
            buf.append(rv)
    return zscores


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------


async def run_backtest(
    db_dsn: str,
    thresholds_path: str,
    symbols_path: str,
    window_days: int,
    csv_path: str | None = None,
) -> dict:
    """
    Replay historical features through alert trigger logic.
    Returns a JSON-serialisable report dict.
    Writes CSV to csv_path if provided.
    """
    with open(thresholds_path) as fh:
        thresholds = yaml.safe_load(fh)

    ve_params = _load_vol_expansion_params(thresholds)
    bo_params = BreakoutParams.from_thresholds(thresholds)
    lr_params = LeadershipRotationParams.from_thresholds(thresholds)
    multipliers = SymbolMultipliers.load(symbols_path)

    cooldown_cfg: dict[str, int] = thresholds.get("cooldowns", {}).get("per_alert_type", {})
    persistence_cfg: dict[str, int] = thresholds.get("persistence", {}).get("per_alert_type", {})

    now = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=window_days)
    candle_end = now + timedelta(hours=13)  # +13h buffer for 12h move lookups

    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        computed, cross = await _fetch_features(conn, start, now)
        candle_cache = await _fetch_candle_cache(conn, SYMBOLS, start, candle_end)

    zscores = _build_rv_zscores(computed)

    cooldowns = _InMemoryCooldowns()
    persistence_tracker = _InMemoryPersistence()
    simulated: list[SimulatedAlert] = []

    times = sorted({t for (t, _) in computed} | set(cross))

    for cycle_time in times:
        cross_features = cross.get(cycle_time, {})

        for symbol in SYMBOLS:
            features = computed.get((cycle_time, symbol), {})
            if not features:
                continue
            rv_1h_zscore = zscores.get((cycle_time, symbol))
            multiplier = multipliers.get(symbol)

            simulated.extend(_vol_expansion_signals(
                features, rv_1h_zscore, ve_params, multiplier,
                cycle_time, cooldowns, persistence_tracker,
                cooldown_cfg, persistence_cfg, symbol,
            ))
            simulated.extend(_breakout_signals(
                features, bo_params, multiplier,
                cycle_time, cooldowns, persistence_tracker,
                cooldown_cfg, persistence_cfg, symbol,
            ))

        simulated.extend(_leadership_rotation_signals(
            cross_features, lr_params,
            cycle_time, cooldowns, persistence_tracker,
            cooldown_cfg, persistence_cfg,
        ))

    # Populate price moves from cache
    for alert in simulated:
        sym = alert.symbol or _PROXY_SYMBOL
        p0 = _price_near(candle_cache, sym, alert.fire_time)
        p4 = _price_near(candle_cache, sym, alert.fire_time + timedelta(hours=4))
        p12 = _price_near(candle_cache, sym, alert.fire_time + timedelta(hours=12))
        if p0 and p4:
            alert.move_4h_pct = round((p4 - p0) / p0 * 100, 4)
        if p0 and p12:
            alert.move_12h_pct = round((p12 - p0) / p0 * 100, 4)

    eval_cfg = thresholds.get("eval", {})
    hit_threshold = float(eval_cfg.get("hit_threshold_pct", 1.0))
    min_sample = int(eval_cfg.get("min_sample_size", 5))

    outcome_rows = [
        {
            "alert_type":        a.alert_type,
            "severity":          a.severity,
            "regime_at_trigger": None,
            "move_4h_pct":       a.move_4h_pct,
            "move_12h_pct":      a.move_12h_pct,
            "has_4h":            a.move_4h_pct is not None,
            "has_12h":           a.move_12h_pct is not None,
        }
        for a in simulated
    ]

    metrics = aggregate_rows(outcome_rows, hit_threshold, min_sample)

    if csv_path:
        Path(csv_path).write_text(_build_csv(simulated))
        log.info("ev4_backtest.csv_written", path=csv_path, count=len(simulated))

    report = {
        "generated_at":          now.isoformat(),
        "range_start":           start.isoformat(),
        "range_end":             now.isoformat(),
        "window_days":           window_days,
        "config_hash":           config_hash(thresholds_path, symbols_path),
        "supported_alert_types": list(_SUPPORTED_TYPES),
        **metrics,
    }

    log.info(
        "ev4_backtest.done",
        window_days=window_days,
        total_simulated=len(simulated),
        feature_timestamps=len(times),
    )
    return report


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def _build_csv(alerts: list[SimulatedAlert]) -> str:
    """Return CSV string with one row per simulated alert."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["time", "alert_type", "symbol", "direction", "severity",
                    "followed_by_move", "move_4h_pct", "move_12h_pct"],
    )
    writer.writeheader()
    for a in alerts:
        writer.writerow({
            "time":             a.fire_time.isoformat(),
            "alert_type":       a.alert_type,
            "symbol":           a.symbol or "market-wide",
            "direction":        a.direction,
            "severity":         a.severity,
            "followed_by_move": a.move_4h_pct is not None,
            "move_4h_pct":      a.move_4h_pct,
            "move_12h_pct":     a.move_12h_pct,
        })
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Param loader (avoids importing private VolExpansionParams.from_thresholds)
# ---------------------------------------------------------------------------


def _load_vol_expansion_params(thresholds: dict) -> object:
    """Load VolExpansionParams from thresholds dict."""
    from alerts.vol_expansion import VolExpansionParams
    return VolExpansionParams.from_thresholds(thresholds)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EV-4: Replay historical features through alert trigger logic."
    )
    parser.add_argument("--days", type=int, default=30,
                        help="Lookback window in days (default: 30)")
    parser.add_argument("--out", type=str, default=None,
                        help="Write JSON report to file path")
    parser.add_argument("--csv", type=str, default=None,
                        help="Write simulated alerts to CSV file path")
    args = parser.parse_args()

    settings = Settings()
    report = asyncio.run(
        run_backtest(
            settings.db_dsn,
            settings.thresholds_path,
            settings.symbols_path,
            args.days,
            csv_path=args.csv,
        )
    )

    output = json.dumps(report, indent=2)
    print(output)

    if args.out:
        Path(args.out).write_text(output)
        log.info("ev4_backtest.report_written", path=args.out)


if __name__ == "__main__":
    main()
