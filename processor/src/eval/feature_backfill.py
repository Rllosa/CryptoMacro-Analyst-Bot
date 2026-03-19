"""
SOLO-143: EV-4b Feature History Backfill

Populates computed_features and cross_features from existing market_candles
so EV-4 backtesting can run on real historical data.

The live feature engine writes only to Redis (ephemeral). This CLI reuses
the same pure compute functions (compute_all_features, compute_all_cross_features)
to replay the full candle history into the DB.

Macro fields (macro_stress, vix, dxy_momentum) are intentionally skipped —
no historical Redis data exists for those inputs.

Idempotent: ON CONFLICT DO NOTHING in all upsert functions.

Usage:
    cd processor
    .venv/bin/python -m eval.feature_backfill [--days 30] [--chunk-size 500]
"""

from __future__ import annotations

import argparse
import asyncio
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import psycopg
import structlog
import yaml

from backfill import SYMBOLS
from config import Settings
from cross_features.engine import _ASSETS_INVOLVED
from cross_features.indicators import compute_all_cross_features
from features.config import FeatureParams
from features.db import MIN_CANDLES
from features.indicators import compute_all_features

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# SQL — hoisted at module level
# ---------------------------------------------------------------------------

_FETCH_CANDLES_SQL = """
    SELECT time, symbol, open, high, low, close, volume
    FROM market_candles
    WHERE symbol = ANY(%s)
      AND timeframe = '5m'
      AND time >= %s
    ORDER BY symbol, time ASC
"""

# ---------------------------------------------------------------------------
# Pure helpers — testable without DB
# ---------------------------------------------------------------------------


def build_computed_rows(
    symbol: str,
    df: pd.DataFrame,
    params: FeatureParams,
    chunk_size: int,
) -> list[list[tuple]]:
    """
    Compute features for every timestamp in df and return batched EAV rows.

    For each row index i, slices the preceding MIN_CANDLES rows and calls
    compute_all_features — identical semantics to the live FeatureEngine.

    Returns a list of chunks, each a list of (time, symbol, feature_name, value, None).
    """
    n = len(df)
    all_rows: list[tuple] = []

    for i in range(n):
        slice_start = max(0, i - MIN_CANDLES + 1)
        window = df.iloc[slice_start : i + 1]
        if len(window) < params.bollinger_period:
            continue  # not enough data — same gate as live engine

        cycle_time = df.index[i]
        features = compute_all_features(window, params)

        for name, value in features.items():
            if not math.isnan(value):
                all_rows.append((cycle_time, symbol, name, value, None))

    return [all_rows[i : i + chunk_size * 30] for i in range(0, len(all_rows), chunk_size * 30)]


def build_cross_rows(
    closes_wide: pd.DataFrame,
    params: FeatureParams,
    chunk_size: int,
) -> list[list[tuple]]:
    """
    Compute cross-asset features for every timestamp in closes_wide.

    n_candles = rs_zscore_window + rs_lookback — same as CrossFeatureEngine.
    Macro fields are skipped (no historical Redis data).

    Returns batched (time, feature_name, value, assets_involved, None) rows.
    """
    n_candles = params.rs_zscore_window + params.rs_lookback
    n = len(closes_wide)
    all_rows: list[tuple] = []

    for i in range(n):
        slice_start = max(0, i - n_candles + 1)
        window = closes_wide.iloc[slice_start : i + 1]
        if len(window) < 2:
            continue

        cycle_time = closes_wide.index[i]
        features = compute_all_cross_features(window, params)

        for name, value in features.items():
            if not math.isnan(value):
                assets = _ASSETS_INVOLVED.get(name, [])
                all_rows.append((cycle_time, name, value, assets, None))

    return [all_rows[i : i + chunk_size * 6] for i in range(0, len(all_rows), chunk_size * 6)]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_INSERT_COMPUTED_SQL_TMPL = (
    "INSERT INTO computed_features (time, symbol, feature_name, value, metadata) "
    "VALUES {placeholders} ON CONFLICT (time, symbol, feature_name) DO NOTHING"
)
_INSERT_CROSS_SQL_TMPL = (
    "INSERT INTO cross_features (time, feature_name, value, assets_involved, metadata) "
    "VALUES {placeholders} ON CONFLICT (time, feature_name) DO NOTHING"
)


async def _upsert_batch(
    conn: psycopg.AsyncConnection,
    sql_tmpl: str,
    rows: list[tuple],
    cols_per_row: int,
) -> int:
    if not rows:
        return 0
    ph = ", ".join(f"({', '.join(['%s'] * cols_per_row)})" for _ in rows)
    flat = [v for row in rows for v in row]
    await conn.execute(sql_tmpl.format(placeholders=ph), flat)
    return len(rows)


# ---------------------------------------------------------------------------
# Main backfill runner
# ---------------------------------------------------------------------------


async def run_backfill(
    db_dsn: str,
    thresholds_path: str,
    window_days: int,
    chunk_size: int,
) -> dict:
    """
    Backfill computed_features and cross_features from market_candles.
    Returns a summary dict.
    """
    with open(thresholds_path) as fh:
        thresholds = yaml.safe_load(fh)
    params = FeatureParams.from_thresholds(thresholds)

    now = datetime.now(tz=timezone.utc)
    # Fetch extra candles before window_start so early timestamps have a full buffer
    fetch_start = now - timedelta(days=window_days) - timedelta(minutes=MIN_CANDLES * 5)

    log.info("feature_backfill.starting", window_days=window_days, fetch_start=fetch_start.isoformat())

    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        # Single query for all symbols
        async with conn.cursor() as cur:
            await cur.execute(_FETCH_CANDLES_SQL, (SYMBOLS, fetch_start))
            raw = await cur.fetchall()

    if not raw:
        log.warning("feature_backfill.no_candles")
        return {"computed_rows": 0, "cross_rows": 0, "symbols": []}

    # Build per-symbol DataFrames
    df_all = pd.DataFrame(raw, columns=["time", "symbol", "open", "high", "low", "close", "volume"])
    df_all["time"] = pd.to_datetime(df_all["time"], utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df_all[col] = df_all[col].astype("float64")

    total_computed = 0
    total_cross = 0

    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        # --- computed_features: per symbol ---
        for symbol in SYMBOLS:
            sym_df = (
                df_all[df_all["symbol"] == symbol]
                .set_index("time")
                .drop(columns="symbol")
                .sort_index()
            )
            if sym_df.empty:
                log.warning("feature_backfill.no_candles_for_symbol", symbol=symbol)
                continue

            chunks = build_computed_rows(symbol, sym_df, params, chunk_size)
            sym_rows = 0
            for chunk in chunks:
                sym_rows += await _upsert_batch(conn, _INSERT_COMPUTED_SQL_TMPL, chunk, 5)
            await conn.commit()

            log.info("feature_backfill.symbol_done", symbol=symbol, rows=sym_rows)
            total_computed += sym_rows

        # --- cross_features: all symbols together ---
        closes_wide = (
            df_all[["time", "symbol", "close"]]
            .pivot(index="time", columns="symbol", values="close")
            .sort_index()
        )
        cross_chunks = build_cross_rows(closes_wide, params, chunk_size)
        for chunk in cross_chunks:
            total_cross += await _upsert_batch(conn, _INSERT_CROSS_SQL_TMPL, chunk, 5)
        await conn.commit()

        log.info("feature_backfill.cross_done", rows=total_cross)

    summary = {
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "window_days": window_days,
        "computed_rows_written": total_computed,
        "cross_rows_written": total_cross,
        "symbols": SYMBOLS,
    }
    log.info("feature_backfill.done", **{k: v for k, v in summary.items() if k != "symbols"})
    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SOLO-143: Backfill computed_features + cross_features from market_candles."
    )
    parser.add_argument("--days", type=int, default=30,
                        help="Lookback window in days (default: 30)")
    parser.add_argument("--chunk-size", type=int, default=500,
                        help="Timestamps per DB upsert batch (default: 500)")
    args = parser.parse_args()

    settings = Settings()
    summary = asyncio.run(
        run_backfill(
            settings.db_dsn,
            settings.thresholds_path,
            args.days,
            args.chunk_size,
        )
    )
    import json
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
