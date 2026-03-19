"""
SOLO-143: EV-4b Feature History Backfill — unit tests.

All tests are pure-logic (no DB). Validates that build_computed_rows and
build_cross_rows produce correct EAV tuples from synthetic candle DataFrames.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import math

import pytest

from eval.feature_backfill import build_computed_rows, build_cross_rows
from features.config import FeatureParams


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_5M = timedelta(minutes=5)


def _make_params() -> FeatureParams:
    """Minimal FeatureParams with defaults sufficient for tests."""
    return FeatureParams(
        rsi_period=14,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        bollinger_period=20,
        bollinger_std=2.0,
        atr_period=14,
        rv_window_1h=12,
        rv_window_4h=48,
        volume_zscore_window=288,
        rs_lookback=20,
        rs_zscore_window=60,
        ema_slope_period=20,
        breakout_4h_window=48,
        breakout_24h_window=288,
    )


def _make_candles(n: int, base_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    """
    Generate n rows of synthetic 5m candles with realistic OHLCV structure.
    Time index is ascending from _T0.
    """
    rng = np.random.default_rng(seed)
    times = [_T0 + i * _5M for i in range(n)]
    closes = base_price + np.cumsum(rng.normal(0, 0.5, n))
    highs = closes + rng.uniform(0.1, 1.0, n)
    lows = closes - rng.uniform(0.1, 1.0, n)
    opens = closes + rng.normal(0, 0.3, n)
    volumes = rng.uniform(100, 1000, n)

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=pd.DatetimeIndex(times, tz=timezone.utc),
    )
    return df.astype("float64")


def _make_closes_wide(n: int) -> pd.DataFrame:
    """Wide DataFrame of 4 symbol closes for cross-feature tests."""
    times = [_T0 + i * _5M for i in range(n)]
    rng = np.random.default_rng(99)
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT"]
    data = {s: 100.0 + np.cumsum(rng.normal(0, 0.5, n)) for s in symbols}
    return pd.DataFrame(data, index=pd.DatetimeIndex(times, tz=timezone.utc))


# ---------------------------------------------------------------------------
# build_computed_rows
# ---------------------------------------------------------------------------


class TestBuildComputedRows:
    _params = _make_params()

    def test_returns_no_rows_when_insufficient_data(self):
        """Fewer than bollinger_period (20) candles → nothing computed."""
        df = _make_candles(15)
        chunks = build_computed_rows("BTCUSDT", df, self._params, chunk_size=500)
        total = sum(len(c) for c in chunks)
        assert total == 0

    def test_produces_rows_with_enough_candles(self):
        """300 candles → rows produced for later timestamps."""
        df = _make_candles(300)
        chunks = build_computed_rows("BTCUSDT", df, self._params, chunk_size=500)
        total = sum(len(c) for c in chunks)
        assert total > 0

    def test_row_structure(self):
        """Each row is a 5-tuple: (time, symbol, feature_name, value, None)."""
        df = _make_candles(300)
        chunks = build_computed_rows("BTCUSDT", df, self._params, chunk_size=500)
        sample = chunks[0][0]
        assert len(sample) == 5
        time_, symbol, name, value, meta = sample
        assert isinstance(symbol, str)
        assert symbol == "BTCUSDT"
        assert isinstance(name, str)
        assert isinstance(value, float)
        assert meta is None

    def test_no_nan_values_in_rows(self):
        """NaN values must never appear in rows (filtered by build_computed_rows)."""
        df = _make_candles(300)
        chunks = build_computed_rows("BTCUSDT", df, self._params, chunk_size=500)
        for chunk in chunks:
            for _, _, _, value, _ in chunk:
                assert not (value != value), f"NaN found in row"  # NaN != NaN

    def test_expected_feature_names_present(self):
        """Core feature names must appear in output."""
        df = _make_candles(300)
        chunks = build_computed_rows("BTCUSDT", df, self._params, chunk_size=500)
        all_rows = [row for chunk in chunks for row in chunk]
        names = {row[2] for row in all_rows}
        for expected in ("volume_zscore", "rv_1h", "breakout_4h_high", "rsi_14", "bb_bandwidth"):
            assert expected in names, f"Missing feature: {expected}"

    def test_breakout_flags_are_zero_or_one(self):
        """Breakout flags must be 0.0 or 1.0."""
        df = _make_candles(300)
        chunks = build_computed_rows("BTCUSDT", df, self._params, chunk_size=500)
        all_rows = [row for chunk in chunks for row in chunk]
        for _, _, name, value, _ in all_rows:
            if name.startswith("breakout_"):
                assert value in (0.0, 1.0), f"{name}={value} is not 0 or 1"

    def test_chunking_covers_all_rows(self):
        """All rows returned regardless of chunk_size."""
        df = _make_candles(300)
        chunks_big = build_computed_rows("BTCUSDT", df, self._params, chunk_size=500)
        chunks_small = build_computed_rows("BTCUSDT", df, self._params, chunk_size=10)
        total_big = sum(len(c) for c in chunks_big)
        total_small = sum(len(c) for c in chunks_small)
        assert total_big == total_small

    def test_idempotent_output(self):
        """Same candles → identical rows on two calls."""
        df = _make_candles(300)
        r1 = [row for chunk in build_computed_rows("ETHUSDT", df, self._params, 500) for row in chunk]
        r2 = [row for chunk in build_computed_rows("ETHUSDT", df, self._params, 500) for row in chunk]
        assert r1 == r2


# ---------------------------------------------------------------------------
# build_cross_rows
# ---------------------------------------------------------------------------


class TestBuildCrossRows:
    _params = _make_params()

    def test_returns_no_rows_when_too_few_candles(self):
        """1 row → can't compute RS (needs at least 2)."""
        closes = _make_closes_wide(1)
        chunks = build_cross_rows(closes, self._params, chunk_size=500)
        total = sum(len(c) for c in chunks)
        assert total == 0

    def test_produces_rows_with_enough_candles(self):
        """100 candles → RS features appear."""
        closes = _make_closes_wide(100)
        chunks = build_cross_rows(closes, self._params, chunk_size=500)
        total = sum(len(c) for c in chunks)
        assert total > 0

    def test_row_structure(self):
        """Each row is a 5-tuple: (time, feature_name, value, assets_involved, None)."""
        closes = _make_closes_wide(100)
        chunks = build_cross_rows(closes, self._params, chunk_size=500)
        sample = chunks[0][0]
        assert len(sample) == 5
        time_, name, value, assets, meta = sample
        assert isinstance(name, str)
        assert isinstance(value, float)
        assert isinstance(assets, list)
        assert meta is None

    def test_expected_feature_names_present(self):
        """RS feature names for all three pairs must appear."""
        closes = _make_closes_wide(100)
        chunks = build_cross_rows(closes, self._params, chunk_size=500)
        all_rows = [row for chunk in chunks for row in chunk]
        names = {row[1] for row in all_rows}
        for expected in ("eth_btc_rs", "eth_btc_rs_zscore", "sol_btc_rs", "hype_btc_rs_zscore"):
            assert expected in names, f"Missing cross feature: {expected}"

    def test_macro_fields_absent(self):
        """macro_stress, vix, dxy_momentum must not appear (no historical Redis data)."""
        closes = _make_closes_wide(100)
        chunks = build_cross_rows(closes, self._params, chunk_size=500)
        all_rows = [row for chunk in chunks for row in chunk]
        names = {row[1] for row in all_rows}
        for forbidden in ("macro_stress", "vix", "dxy_momentum"):
            assert forbidden not in names, f"Macro field should not be in backfill: {forbidden}"

    def test_zscore_values_are_finite(self):
        """eth_btc_rs_zscore rows exist and are finite floats (not NaN or ±inf)."""
        closes = _make_closes_wide(120)
        chunks = build_cross_rows(closes, self._params, chunk_size=500)
        all_rows = [row for chunk in chunks for row in chunk]
        eth_zscores = [row[2] for row in all_rows if row[1] == "eth_btc_rs_zscore"]
        assert eth_zscores, "No eth_btc_rs_zscore rows found"
        assert all(isinstance(z, float) and not math.isinf(z) for z in eth_zscores)

    def test_no_nan_in_rows(self):
        closes = _make_closes_wide(100)
        chunks = build_cross_rows(closes, self._params, chunk_size=500)
        for chunk in chunks:
            for _, _, value, _, _ in chunk:
                assert not (value != value), "NaN found in cross row"
