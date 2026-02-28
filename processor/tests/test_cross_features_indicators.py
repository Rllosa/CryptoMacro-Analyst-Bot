"""Unit tests for cross_features/indicators.py.

Covers compute_rs_and_zscore and compute_all_cross_features with property-based
assertions: sign, magnitude bounds, edge cases, and NaN propagation.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from cross_features.indicators import compute_all_cross_features, compute_rs_and_zscore
from features.config import FeatureParams

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_LOOKBACK = 20
_ZSCORE_WIN = 60
_N = _LOOKBACK + _ZSCORE_WIN  # 80 candles total


@pytest.fixture(name="default_params")
def fixture_default_params() -> FeatureParams:
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
        rs_lookback=_LOOKBACK,
        rs_zscore_window=_ZSCORE_WIN,
        ema_slope_period=20,
        breakout_4h_window=48,
        breakout_24h_window=288,
    )


def _flat_closes(n: int = _N) -> pd.DataFrame:
    """All four symbols at constant price — zero RS everywhere."""
    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    return pd.DataFrame(
        {"BTCUSDT": [50000.0] * n, "ETHUSDT": [3000.0] * n,
         "SOLUSDT": [200.0] * n, "HYPEUSDT": [20.0] * n},
        index=idx,
    )


def _surge_eth_closes(n: int = _N) -> pd.DataFrame:
    """
    ETH flat for first 60 candles, then rises +10 per period for last 20.
    Creates a leadership rotation scenario with positive RS and z-score > 2.
    """
    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    eth = [1000.0] * _ZSCORE_WIN + [1000.0 + i * 10.0 for i in range(1, _LOOKBACK + 1)]
    return pd.DataFrame(
        {"BTCUSDT": [50000.0] * n, "ETHUSDT": eth,
         "SOLUSDT": [200.0] * n, "HYPEUSDT": [20.0] * n},
        index=idx,
    )


def _decline_eth_closes(n: int = _N) -> pd.DataFrame:
    """Mirror of _surge_eth_closes: ETH declines after flat period → negative RS / z-score."""
    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    # Start flat at 1200 then decline so RS mirrors the surge case exactly
    eth = [1200.0] * _ZSCORE_WIN + [1200.0 - i * 12.0 for i in range(1, _LOOKBACK + 1)]
    return pd.DataFrame(
        {"BTCUSDT": [50000.0] * n, "ETHUSDT": eth,
         "SOLUSDT": [200.0] * n, "HYPEUSDT": [20.0] * n},
        index=idx,
    )


# ---------------------------------------------------------------------------
# TestComputeRsAndZscore
# ---------------------------------------------------------------------------

class TestComputeRsAndZscore:
    def test_equal_performance_zero_rs(self) -> None:
        """Identical price series → RS = 0, z-score = 0."""
        s = pd.Series([1000.0] * _N)
        rs, z = compute_rs_and_zscore(s, s.copy(), _LOOKBACK, _ZSCORE_WIN)
        assert rs == pytest.approx(0.0, abs=1e-10)
        assert z == 0.0  # std < 1e-10 path

    def test_eth_outperforms_btc_positive_rs(self) -> None:
        """ETH consistently growing faster than flat BTC → positive RS."""
        btc = pd.Series([50000.0] * _N)
        eth = pd.Series([1000.0 + i * 5.0 for i in range(_N)])
        rs, _ = compute_rs_and_zscore(eth, btc, _LOOKBACK, _ZSCORE_WIN)
        assert rs > 0.0

    def test_eth_underperforms_btc_negative_rs(self) -> None:
        """ETH declining vs flat BTC → negative RS."""
        btc = pd.Series([50000.0] * _N)
        eth = pd.Series([2000.0 - i * 5.0 for i in range(_N)])
        rs, _ = compute_rs_and_zscore(eth, btc, _LOOKBACK, _ZSCORE_WIN)
        assert rs < 0.0

    def test_zscore_positive_when_current_rs_above_mean(self) -> None:
        """Surge after flat period → current RS > historical mean → z > 0."""
        btc = pd.Series([50000.0] * _N)
        eth = pd.Series([1000.0] * _ZSCORE_WIN + [1000.0 + i * 10.0 for i in range(1, _LOOKBACK + 1)])
        rs, z = compute_rs_and_zscore(eth, btc, _LOOKBACK, _ZSCORE_WIN)
        assert rs > 0.0
        assert z > 0.0

    def test_zscore_negative_when_current_rs_below_mean(self) -> None:
        """Decline after flat period → current RS < historical mean → z < 0."""
        btc = pd.Series([50000.0] * _N)
        # RS was positive early (ETH flat from high) then went to zero or negative
        eth = pd.Series([1200.0] * _ZSCORE_WIN + [1200.0 - i * 12.0 for i in range(1, _LOOKBACK + 1)])
        rs, z = compute_rs_and_zscore(eth, btc, _LOOKBACK, _ZSCORE_WIN)
        assert rs < 0.0
        assert z < 0.0

    def test_leadership_rotation_crosses_threshold(self) -> None:
        """Surge scenario: z-score exceeds the 2.0 leadership rotation threshold."""
        btc = pd.Series([50000.0] * _N)
        eth = pd.Series([1000.0] * _ZSCORE_WIN + [1000.0 + i * 10.0 for i in range(1, _LOOKBACK + 1)])
        _, z = compute_rs_and_zscore(eth, btc, _LOOKBACK, _ZSCORE_WIN)
        assert z > 2.0  # threshold from thresholds.yaml leadership_rotation.conditions.rs_zscore

    def test_zero_variance_rs_returns_zero_zscore(self) -> None:
        """Constant RS → std < 1e-10 → z-score clamped to 0.0, RS returned."""
        btc = pd.Series([50000.0] * _N)
        # ETH grows at exactly 1.001^t → every N-period return is 1.001^20 - 1
        eth = pd.Series([1000.0 * (1.001 ** i) for i in range(_N)])
        rs, z = compute_rs_and_zscore(eth, btc, _LOOKBACK, _ZSCORE_WIN)
        assert not math.isnan(rs)
        assert rs > 0.0
        assert z == 0.0

    def test_insufficient_data_both_nan(self) -> None:
        """Fewer than 2 non-NaN RS values → (nan, nan)."""
        # 1 candle only → shift(_LOOKBACK) leaves nothing after dropna
        s = pd.Series([1000.0])
        rs, z = compute_rs_and_zscore(s, s.copy(), _LOOKBACK, _ZSCORE_WIN)
        assert math.isnan(rs)
        assert math.isnan(z)

    def test_exactly_lookback_plus_one_candles_produces_one_rs(self) -> None:
        """lookback+1 candles → exactly 1 RS value → (nan, nan) because len < 2."""
        s = pd.Series([1000.0] * (_LOOKBACK + 1))
        rs, z = compute_rs_and_zscore(s, s.copy(), _LOOKBACK, _ZSCORE_WIN)
        assert math.isnan(rs)
        assert math.isnan(z)

    def test_lookback_plus_two_candles_produces_valid_result(self) -> None:
        """lookback+2 candles → exactly 2 RS values → valid (rs, z)."""
        s = pd.Series([1000.0] * (_LOOKBACK + 2))
        rs, z = compute_rs_and_zscore(s, s.copy(), _LOOKBACK, _ZSCORE_WIN)
        # Both series identical → RS=0, z=0 (std ≈ 0)
        assert not math.isnan(rs)
        assert rs == pytest.approx(0.0, abs=1e-10)


# ---------------------------------------------------------------------------
# TestComputeAllCrossFeatures
# ---------------------------------------------------------------------------

class TestComputeAllCrossFeatures:
    def test_all_six_features_returned(self, default_params: FeatureParams) -> None:
        """Full data → dict contains exactly 6 keys (3 RS, 3 z-score). macro_stress is
        computed separately by CrossFeatureEngine via FE-3 and merged in after this call."""
        closes = _flat_closes()
        features = compute_all_cross_features(closes, default_params)
        expected_keys = {
            "eth_btc_rs", "eth_btc_rs_zscore",
            "sol_btc_rs", "sol_btc_rs_zscore",
            "hype_btc_rs", "hype_btc_rs_zscore",
        }
        assert set(features.keys()) == expected_keys

    def test_no_correlation_features_in_output(self, default_params: FeatureParams) -> None:
        """Correlation stubs are absent — not inserted until FE-3 provides macro data."""
        closes = _flat_closes()
        features = compute_all_cross_features(closes, default_params)
        for key in ("corr_btc_sp500", "corr_btc_dxy", "corr_btc_sp500_7d"):
            assert key not in features

    def test_flat_prices_all_rs_zero(self, default_params: FeatureParams) -> None:
        """All symbols constant → RS = 0, z = 0 for all pairs."""
        closes = _flat_closes()
        features = compute_all_cross_features(closes, default_params)
        for prefix in ("eth_btc", "sol_btc", "hype_btc"):
            assert features[f"{prefix}_rs"] == pytest.approx(0.0, abs=1e-10)
            assert features[f"{prefix}_rs_zscore"] == 0.0

    def test_eth_surge_positive_rs_and_zscore(self, default_params: FeatureParams) -> None:
        """ETH surge → eth_btc RS and z-score both positive."""
        closes = _surge_eth_closes()
        features = compute_all_cross_features(closes, default_params)
        assert features["eth_btc_rs"] > 0.0
        assert features["eth_btc_rs_zscore"] > 2.0
        # SOL and HYPE flat → near-zero RS
        assert features["sol_btc_rs"] == pytest.approx(0.0, abs=1e-10)
        assert features["hype_btc_rs"] == pytest.approx(0.0, abs=1e-10)

    def test_eth_decline_negative_rs_and_zscore(self, default_params: FeatureParams) -> None:
        """ETH decline → eth_btc RS and z-score both negative."""
        closes = _decline_eth_closes()
        features = compute_all_cross_features(closes, default_params)
        assert features["eth_btc_rs"] < 0.0
        assert features["eth_btc_rs_zscore"] < -2.0

    def test_missing_btc_column_all_nan(self, default_params: FeatureParams) -> None:
        """No BTCUSDT column → all RS and z-score values are NaN."""
        closes = _flat_closes().drop(columns=["BTCUSDT"])
        features = compute_all_cross_features(closes, default_params)
        for prefix in ("eth_btc", "sol_btc", "hype_btc"):
            assert math.isnan(features[f"{prefix}_rs"])
            assert math.isnan(features[f"{prefix}_rs_zscore"])

    def test_missing_alt_column_only_that_pair_nan(self, default_params: FeatureParams) -> None:
        """Missing ETHUSDT → only eth_btc pair is NaN; sol/hype computed normally."""
        closes = _flat_closes().drop(columns=["ETHUSDT"])
        features = compute_all_cross_features(closes, default_params)
        assert math.isnan(features["eth_btc_rs"])
        assert math.isnan(features["eth_btc_rs_zscore"])
        # SOL and HYPE still computed
        assert not math.isnan(features["sol_btc_rs"])
        assert not math.isnan(features["hype_btc_rs"])
