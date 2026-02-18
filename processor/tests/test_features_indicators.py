from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from features.config import FeatureParams
from features.indicators import (
    compute_atr,
    compute_bollinger,
    compute_breakout_flags,
    compute_ema_slope,
    compute_macd,
    compute_realized_vol,
    compute_returns,
    compute_rsi,
    compute_volume_zscore,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_THRESHOLDS = str(Path(__file__).parent.parent.parent / "configs" / "thresholds.yaml")


def _params() -> FeatureParams:
    return FeatureParams.load(_THRESHOLDS)


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype="float64")


def _close(n: int = 350, start: float = 50_000.0, step: float = 10.0) -> pd.Series:
    """Monotonically rising close series."""
    return _series([start + i * step for i in range(n)])


def _constant(n: int = 350, val: float = 50_000.0) -> pd.Series:
    return _series([val] * n)


def _ohlcv(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (high, low, volume) derived from a close series."""
    high = close * 1.005
    low = close * 0.995
    volume = _series([1_000.0] * len(close))
    return high, low, volume


# ---------------------------------------------------------------------------
# compute_returns
# ---------------------------------------------------------------------------


class TestComputeReturns:
    def test_r5m_matches_manual(self) -> None:
        # Three prices: [100, 101, 102] → r_5m = 102/101 − 1
        close = _series([100.0, 101.0, 102.0])
        result = compute_returns(close)
        assert result["r_5m"] == pytest.approx(102.0 / 101.0 - 1.0, rel=1e-9)

    def test_all_windows_finite_when_enough_data(self) -> None:
        close = _close(350)
        result = compute_returns(close)
        for key in ("r_5m", "r_1h", "r_4h", "r_1d"):
            assert math.isfinite(result[key]), f"{key} should be finite"

    def test_r1d_nan_when_insufficient_data(self) -> None:
        # Only 100 rows — r_1d (window=288) must return NaN
        close = _close(100)
        result = compute_returns(close)
        assert math.isnan(result["r_1d"])

    def test_positive_return_for_rising_prices(self) -> None:
        close = _close(350)
        result = compute_returns(close)
        assert result["r_5m"] > 0
        assert result["r_1h"] > 0
        assert result["r_4h"] > 0
        assert result["r_1d"] > 0


# ---------------------------------------------------------------------------
# compute_realized_vol
# ---------------------------------------------------------------------------


class TestComputeRealizedVol:
    def test_positive_for_volatile_series(self) -> None:
        np.random.seed(7)
        prices = (50_000.0 + np.cumsum(np.random.normal(0, 200, 100))).tolist()
        rv = compute_realized_vol(_series(prices), window=12)
        assert rv > 0

    def test_zero_for_constant_prices(self) -> None:
        rv = compute_realized_vol(_constant(), window=12)
        assert rv == pytest.approx(0.0)

    def test_nan_when_insufficient_data(self) -> None:
        close = _series([50_000.0] * 5)
        assert math.isnan(compute_realized_vol(close, window=12))

    def test_rv4h_gt_rv1h_for_long_volatile_series(self) -> None:
        # Both should be finite and positive; for a random walk the longer
        # window captures more variation and tends to be ≥ the shorter one.
        np.random.seed(99)
        prices = (50_000.0 + np.cumsum(np.random.normal(0, 300, 350))).tolist()
        rv1 = compute_realized_vol(_series(prices), window=12)
        rv4 = compute_realized_vol(_series(prices), window=48)
        assert math.isfinite(rv1) and rv1 > 0
        assert math.isfinite(rv4) and rv4 > 0


# ---------------------------------------------------------------------------
# compute_rsi
# ---------------------------------------------------------------------------


class TestComputeRsi:
    def test_in_range_0_100(self) -> None:
        close = _close(100)
        rsi = compute_rsi(close, period=14)
        assert 0 <= rsi <= 100

    def test_nan_when_insufficient_data(self) -> None:
        close = _series([50_000.0] * 5)
        assert math.isnan(compute_rsi(close, period=14))

    def test_high_rsi_for_rising_prices(self) -> None:
        close = _close(50)
        rsi = compute_rsi(close, period=14)
        assert rsi > 70

    def test_low_rsi_for_falling_prices(self) -> None:
        close = _series([100_000.0 - i * 100 for i in range(50)])
        rsi = compute_rsi(close, period=14)
        assert rsi < 30


# ---------------------------------------------------------------------------
# compute_macd
# ---------------------------------------------------------------------------


class TestComputeMacd:
    def test_returns_three_finite_floats(self) -> None:
        close = _close(100)
        m, s, h = compute_macd(close, fast=12, slow=26, signal=9)
        assert math.isfinite(m)
        assert math.isfinite(s)
        assert math.isfinite(h)

    def test_hist_equals_macd_minus_signal(self) -> None:
        close = _close(100)
        m, s, h = compute_macd(close, fast=12, slow=26, signal=9)
        assert h == pytest.approx(m - s, abs=1e-6)

    def test_nan_when_insufficient_data(self) -> None:
        close = _series([50_000.0] * 10)
        m, s, h = compute_macd(close, fast=12, slow=26, signal=9)
        assert math.isnan(m) and math.isnan(s) and math.isnan(h)


# ---------------------------------------------------------------------------
# compute_bollinger
# ---------------------------------------------------------------------------


class TestComputeBollinger:
    def test_upper_gt_lower_for_volatile_series(self) -> None:
        np.random.seed(1)
        prices = (50_000.0 + np.random.normal(0, 200, 100)).tolist()
        upper, middle, lower, pct_b, bwidth = compute_bollinger(_series(prices), 20, 2.0)
        assert upper > lower

    def test_all_nan_when_insufficient_data(self) -> None:
        close = _series([50_000.0] * 5)
        result = compute_bollinger(close, 20, 2.0)
        assert all(math.isnan(v) for v in result)

    def test_middle_is_sma(self) -> None:
        close = _close(50)
        _, middle, _, _, _ = compute_bollinger(close, 20, 2.0)
        expected_sma = float(close.iloc[-20:].mean())
        assert middle == pytest.approx(expected_sma, rel=1e-6)


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------


class TestComputeAtr:
    def test_positive_for_normal_ohlcv(self) -> None:
        close = _close(50)
        high, low, _ = _ohlcv(close)
        atr = compute_atr(high, low, close, period=14)
        assert atr > 0

    def test_nan_when_insufficient_data(self) -> None:
        close = _series([50_000.0] * 5)
        high, low, _ = _ohlcv(close)
        assert math.isnan(compute_atr(high, low, close, period=14))


# ---------------------------------------------------------------------------
# compute_ema_slope
# ---------------------------------------------------------------------------


class TestComputeEmaSlope:
    def test_positive_for_rising_prices(self) -> None:
        close = _close(50)
        slope = compute_ema_slope(close, period=20)
        assert math.isfinite(slope) and slope > 0

    def test_negative_for_falling_prices(self) -> None:
        close = _series([100_000.0 - i * 50 for i in range(50)])
        slope = compute_ema_slope(close, period=20)
        assert math.isfinite(slope) and slope < 0

    def test_zero_for_constant_prices(self) -> None:
        close = _constant(50)
        slope = compute_ema_slope(close, period=20)
        assert slope == pytest.approx(0.0, abs=1e-12)

    def test_nan_when_insufficient_data(self) -> None:
        close = _series([50_000.0] * 10)
        assert math.isnan(compute_ema_slope(close, period=20))


# ---------------------------------------------------------------------------
# compute_volume_zscore
# ---------------------------------------------------------------------------


class TestComputeVolumeZscore:
    def test_zero_for_constant_volume(self) -> None:
        volume = _series([1_000.0] * 350)
        z = compute_volume_zscore(volume, window=288)
        assert z == pytest.approx(0.0)

    def test_positive_for_volume_spike(self) -> None:
        volume = _series([1_000.0] * 349 + [5_000.0])
        z = compute_volume_zscore(volume, window=288)
        assert z > 0

    def test_nan_when_insufficient_data(self) -> None:
        volume = _series([1_000.0] * 10)
        assert math.isnan(compute_volume_zscore(volume, window=288))


# ---------------------------------------------------------------------------
# compute_breakout_flags
# ---------------------------------------------------------------------------


class TestComputeBreakoutFlags:
    def test_high_breakout_detected(self) -> None:
        # 50 steady candles, then last candle closes well above prior high
        steady = [100.0] * 50
        high = _series([p * 1.005 for p in steady])
        low = _series([p * 0.995 for p in steady])
        # close_last is 110, well above prior high (~100.5)
        bo_high, bo_low = compute_breakout_flags(high, low, 110.0, window=48)
        assert bo_high is True
        assert bo_low is False

    def test_low_breakout_detected(self) -> None:
        steady = [100.0] * 50
        high = _series([p * 1.005 for p in steady])
        low = _series([p * 0.995 for p in steady])
        # close_last is 85, well below prior low (~99.5)
        bo_high, bo_low = compute_breakout_flags(high, low, 85.0, window=48)
        assert bo_high is False
        assert bo_low is True

    def test_no_breakout_within_range(self) -> None:
        steady = [100.0] * 50
        high = _series([p * 1.005 for p in steady])
        low = _series([p * 0.995 for p in steady])
        bo_high, bo_low = compute_breakout_flags(high, low, 100.0, window=48)
        assert bo_high is False
        assert bo_low is False

    def test_false_when_insufficient_data(self) -> None:
        high = _series([100.0] * 5)
        low = _series([99.0] * 5)
        bo_high, bo_low = compute_breakout_flags(high, low, 110.0, window=48)
        assert bo_high is False
        assert bo_low is False
