"""
Golden fixture tests for compute_all_features.

Three deterministic test vectors verify that the full feature computation
pipeline produces byte-identical output for known inputs. Golden values were
computed once by running the function with these exact inputs, then hard-coded.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from features.config import FeatureParams
from features.indicators import compute_all_features

_THRESHOLDS = str(Path(__file__).parent.parent.parent / "configs" / "thresholds.yaml")


def _params() -> FeatureParams:
    return FeatureParams.load(_THRESHOLDS)


def _make_df(prices: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(prices)
    if volumes is None:
        volumes = [1_000.0] * n
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.005 for p in prices],
            "low": [p * 0.995 for p in prices],
            "close": prices,
            "volume": volumes,
        },
        dtype="float64",
    )


# ---------------------------------------------------------------------------
# Vector 1 — Trending up
# Steady linear rise: close[i] = 50 000 + i × 10, volume[i] = 1000 + i × 2
# Expected: strong uptrend (RSI=100, positive returns, flat BB, near-zero RV)
# ---------------------------------------------------------------------------

_V1_PRICES = [50_000.0 + i * 10 for i in range(350)]
_V1_VOLUMES = [1_000.0 + i * 2 for i in range(350)]

# Golden values — computed with seed data above, hard-coded for determinism
_V1_GOLDEN: dict[str, float] = {
    "r_5m": 0.0001869857890799409,
    "r_1h": 0.00224845,
    "r_4h": 0.00905490,
    "r_1d": 0.05690575,
    "rsi_14": 100.00000000,
    "bb_upper": 53510.32562595,
    "bb_middle": 53395.00000000,
    "bb_lower": 53279.67437405,
    "macd": 70.00000000,
    "macd_signal": 70.00000000,
    "macd_hist": 0.00000000,
    "ema_slope": 0.00018732,
    "volume_zscore": 1.72304793,
    "breakout_4h_high": 0.0,
    "breakout_4h_low": 0.0,
    "breakout_24h_high": 0.0,
    "breakout_24h_low": 0.0,
}


class TestVector1TrendingUp:
    """Trending-up candles: strong positive returns, RSI pegged at 100."""

    @pytest.fixture(scope="class")
    def features(self) -> dict[str, float]:
        df = _make_df(_V1_PRICES, _V1_VOLUMES)
        return compute_all_features(df, _params())

    def test_r5m_golden(self, features: dict[str, float]) -> None:
        assert features["r_5m"] == pytest.approx(_V1_GOLDEN["r_5m"], rel=1e-5)

    def test_r1h_golden(self, features: dict[str, float]) -> None:
        assert features["r_1h"] == pytest.approx(_V1_GOLDEN["r_1h"], rel=1e-5)

    def test_r1d_golden(self, features: dict[str, float]) -> None:
        assert features["r_1d"] == pytest.approx(_V1_GOLDEN["r_1d"], rel=1e-5)

    def test_rsi_equals_100(self, features: dict[str, float]) -> None:
        assert features["rsi_14"] == pytest.approx(100.0, abs=1e-6)

    def test_bb_upper_gt_lower(self, features: dict[str, float]) -> None:
        assert features["bb_upper"] > features["bb_lower"]

    def test_bb_middle_golden(self, features: dict[str, float]) -> None:
        assert features["bb_middle"] == pytest.approx(_V1_GOLDEN["bb_middle"], rel=1e-6)

    def test_ema_slope_positive(self, features: dict[str, float]) -> None:
        assert features["ema_slope"] > 0

    def test_volume_zscore_positive(self, features: dict[str, float]) -> None:
        # Volume is growing with each candle — latest is above the rolling mean
        assert features["volume_zscore"] > 0

    def test_breakout_flags_all_zero(self, features: dict[str, float]) -> None:
        # Steady rise: close never exceeds prior rolling high because high=close*1.005
        for flag in ("breakout_4h_high", "breakout_4h_low", "breakout_24h_high", "breakout_24h_low"):
            assert features[flag] == 0.0, f"{flag} should be 0"

    def test_macd_golden(self, features: dict[str, float]) -> None:
        assert features["macd"] == pytest.approx(_V1_GOLDEN["macd"], rel=1e-5)

    def test_rv_values_are_finite(self, features: dict[str, float]) -> None:
        assert math.isfinite(features["rv_1h"])
        assert math.isfinite(features["rv_4h"])

    def test_all_22_features_present(self, features: dict[str, float]) -> None:
        expected_names = {
            "r_5m", "r_1h", "r_4h", "r_1d",
            "rv_1h", "rv_4h",
            "rsi_14",
            "macd", "macd_signal", "macd_hist",
            "bb_upper", "bb_middle", "bb_lower", "bb_pct_b", "bb_bandwidth",
            "atr_14",
            "ema_slope",
            "volume_zscore",
            "breakout_4h_high", "breakout_4h_low",
            "breakout_24h_high", "breakout_24h_low",
        }
        assert expected_names == set(features.keys())


# ---------------------------------------------------------------------------
# Vector 2 — Oscillating (sine wave)
# 350-row sine: close[i] = 50 000 + 1 000 × sin(i × 20π / 349)
# Expected: moderate RSI (~50–70), positive/negative cycles, no breakout
# ---------------------------------------------------------------------------

_t = np.linspace(0, 20 * math.pi, 350)
_V2_PRICES = (50_000.0 + 1_000.0 * np.sin(_t)).tolist()

_V2_GOLDEN: dict[str, float] = {
    "r_5m": 0.00359413,
    "rsi_14": 61.07675144,
    "bb_upper": 50245.58396452,
    "bb_lower": 48681.71751131,
    "macd": -110.11290563,
    "macd_signal": -209.93655409,
    "rv_1h": 0.60749653,
}


class TestVector2Oscillating:
    """Sine-wave candles: RSI mid-range, substantial RV, no breakout."""

    @pytest.fixture(scope="class")
    def features(self) -> dict[str, float]:
        df = _make_df(_V2_PRICES)
        return compute_all_features(df, _params())

    def test_r5m_golden(self, features: dict[str, float]) -> None:
        assert features["r_5m"] == pytest.approx(_V2_GOLDEN["r_5m"], rel=1e-5)

    def test_rsi_mid_range(self, features: dict[str, float]) -> None:
        assert 30 < features["rsi_14"] < 80

    def test_rsi_golden(self, features: dict[str, float]) -> None:
        assert features["rsi_14"] == pytest.approx(_V2_GOLDEN["rsi_14"], rel=1e-5)

    def test_bb_upper_gt_lower(self, features: dict[str, float]) -> None:
        assert features["bb_upper"] > features["bb_lower"]

    def test_bb_golden(self, features: dict[str, float]) -> None:
        assert features["bb_upper"] == pytest.approx(_V2_GOLDEN["bb_upper"], rel=1e-5)
        assert features["bb_lower"] == pytest.approx(_V2_GOLDEN["bb_lower"], rel=1e-5)

    def test_rv1h_substantial(self, features: dict[str, float]) -> None:
        # Sine prices have meaningful variance — rv_1h should be > 0
        assert features["rv_1h"] > 0

    def test_rv1h_golden(self, features: dict[str, float]) -> None:
        assert features["rv_1h"] == pytest.approx(_V2_GOLDEN["rv_1h"], rel=1e-5)

    def test_macd_golden(self, features: dict[str, float]) -> None:
        assert features["macd"] == pytest.approx(_V2_GOLDEN["macd"], rel=1e-5)
        assert features["macd_signal"] == pytest.approx(_V2_GOLDEN["macd_signal"], rel=1e-5)

    def test_breakout_flags_all_zero(self, features: dict[str, float]) -> None:
        for flag in ("breakout_4h_high", "breakout_4h_low", "breakout_24h_high", "breakout_24h_low"):
            assert features[flag] == 0.0


# ---------------------------------------------------------------------------
# Vector 3 — High volatility vs flat
# Compares rv_1h between a random-walk series and a flat series.
# rv_1h(high_vol) > rv_1h(flat) must hold.
# Also verifies that constant-price edge cases produce deterministic outputs.
# ---------------------------------------------------------------------------

np.random.seed(7)
_V3_HIGHVOL_PRICES = (50_000.0 + np.cumsum(np.random.normal(0, 500, 350))).tolist()

_V3_HIGHVOL_GOLDEN: dict[str, float] = {
    "rv_1h": 3.96254778,
}

_V3_FLAT_GOLDEN: dict[str, float] = {
    "rv_1h": 0.0,
    "rv_4h": 0.0,
    "bb_bandwidth": 0.0,
    "macd": 0.0,
    "macd_hist": 0.0,
    "r_5m": 0.0,
    "r_1h": 0.0,
    "r_4h": 0.0,
    "r_1d": 0.0,
    "volume_zscore": 0.0,
    "ema_slope": 0.0,
}


class TestVector3HighVolVsFlat:
    """High-vol random walk vs flat prices — rv ordering and edge cases."""

    @pytest.fixture(scope="class")
    def features_hv(self) -> dict[str, float]:
        df = _make_df(_V3_HIGHVOL_PRICES)
        return compute_all_features(df, _params())

    @pytest.fixture(scope="class")
    def features_flat(self) -> dict[str, float]:
        df = _make_df([50_000.0] * 350)
        return compute_all_features(df, _params())

    def test_rv1h_highvol_gt_flat(
        self, features_hv: dict[str, float], features_flat: dict[str, float]
    ) -> None:
        assert features_hv["rv_1h"] > features_flat["rv_1h"]

    def test_rv1h_highvol_golden(self, features_hv: dict[str, float]) -> None:
        assert features_hv["rv_1h"] == pytest.approx(_V3_HIGHVOL_GOLDEN["rv_1h"], rel=1e-5)

    def test_flat_rv_zero(self, features_flat: dict[str, float]) -> None:
        assert features_flat["rv_1h"] == pytest.approx(0.0)
        assert features_flat["rv_4h"] == pytest.approx(0.0)

    def test_flat_returns_zero(self, features_flat: dict[str, float]) -> None:
        for key in ("r_5m", "r_1h", "r_4h", "r_1d"):
            assert features_flat[key] == pytest.approx(0.0)

    def test_flat_macd_zero(self, features_flat: dict[str, float]) -> None:
        assert features_flat["macd"] == pytest.approx(0.0, abs=1e-8)
        assert features_flat["macd_hist"] == pytest.approx(0.0, abs=1e-8)

    def test_flat_volume_zscore_zero(self, features_flat: dict[str, float]) -> None:
        assert features_flat["volume_zscore"] == pytest.approx(0.0)

    def test_flat_ema_slope_zero(self, features_flat: dict[str, float]) -> None:
        assert features_flat["ema_slope"] == pytest.approx(0.0, abs=1e-12)

    def test_determinism(self) -> None:
        """Running compute_all_features twice on the same input must give identical output."""
        df = _make_df(_V3_HIGHVOL_PRICES)
        p = _params()
        f1 = compute_all_features(df, p)
        f2 = compute_all_features(df, p)
        for key in f1:
            v1, v2 = f1[key], f2[key]
            if math.isnan(v1):
                assert math.isnan(v2), f"{key}: first=NaN, second={v2}"
            else:
                assert v1 == v2, f"{key}: {v1} != {v2}"
