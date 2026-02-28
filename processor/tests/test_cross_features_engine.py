"""Golden fixture tests for cross_features/indicators.py.

Three deterministic vectors with pre-computed expected values.
If the computation logic changes, these tests will catch the regression.

Vector 1 — All flat (zero RS baseline):
  All 4 symbols at constant price. RS = 0, z-score = 0 for all pairs.

Vector 2 — ETH leadership rotation (surge):
  BTC constant at 50 000. ETH flat at 1 000 for 60 candles, then rises
  by +10 per candle for the final 20 (60 + 20 = 80 total candles with
  rs_lookback=20, rs_zscore_window=60).
  RS[t=60..79] = i*10/1000 for i=1..20 = [0.01, 0.02, ..., 0.20]
  RS[t=20..59] = 0 (ETH flat over lookback window)
  mean = 2.10/60 = 0.035, current RS = 0.20, z ≈ 2.74

Vector 3 — ETH decline (mirror of Vector 2):
  ETH flat at 1 200 then declines by −12 per candle for final 20.
  RS mirrors Vector 2 exactly, z-score ≈ −2.74.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from cross_features.indicators import compute_all_cross_features, compute_rs_and_zscore
from features.config import FeatureParams

_LOOKBACK = 20
_ZSCORE_WIN = 60
_N = _LOOKBACK + _ZSCORE_WIN  # 80


@pytest.fixture(name="params")
def fixture_params() -> FeatureParams:
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


def _make_closes(btc: list, eth: list, sol: list, hype: list) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(btc), freq="5min")
    return pd.DataFrame(
        {"BTCUSDT": btc, "ETHUSDT": eth, "SOLUSDT": sol, "HYPEUSDT": hype},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Golden fixture data
# ---------------------------------------------------------------------------

@pytest.fixture(name="v1_closes")
def fixture_v1() -> pd.DataFrame:
    """Vector 1: all symbols constant (zero RS everywhere)."""
    return _make_closes(
        btc=[50000.0] * _N,
        eth=[3000.0] * _N,
        sol=[200.0] * _N,
        hype=[20.0] * _N,
    )


@pytest.fixture(name="v2_closes")
def fixture_v2() -> pd.DataFrame:
    """Vector 2: ETH surge (leadership rotation) — positive RS and z-score."""
    eth = [1000.0] * _ZSCORE_WIN + [1000.0 + i * 10.0 for i in range(1, _LOOKBACK + 1)]
    return _make_closes(
        btc=[50000.0] * _N,
        eth=eth,
        sol=[200.0] * _N,
        hype=[20.0] * _N,
    )


@pytest.fixture(name="v3_closes")
def fixture_v3() -> pd.DataFrame:
    """Vector 3: ETH decline — negative RS and z-score (mirror of Vector 2)."""
    # Decline from 1200 by -12 per period → RS mirrors Vector 2 exactly
    eth = [1200.0] * _ZSCORE_WIN + [1200.0 - i * 12.0 for i in range(1, _LOOKBACK + 1)]
    return _make_closes(
        btc=[50000.0] * _N,
        eth=eth,
        sol=[200.0] * _N,
        hype=[20.0] * _N,
    )


# ---------------------------------------------------------------------------
# Vector 1 — Zero RS baseline
# ---------------------------------------------------------------------------

class TestVector1AllFlat:
    def test_eth_btc_rs_is_zero(self, v1_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v1_closes, params)
        assert features["eth_btc_rs"] == pytest.approx(0.0, abs=1e-10)

    def test_eth_btc_rs_zscore_is_zero(self, v1_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v1_closes, params)
        assert features["eth_btc_rs_zscore"] == 0.0  # std < 1e-10 → explicit zero

    def test_sol_btc_rs_is_zero(self, v1_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v1_closes, params)
        assert features["sol_btc_rs"] == pytest.approx(0.0, abs=1e-10)

    def test_hype_btc_rs_is_zero(self, v1_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v1_closes, params)
        assert features["hype_btc_rs"] == pytest.approx(0.0, abs=1e-10)

    def test_determinism(self, v1_closes: pd.DataFrame, params: FeatureParams) -> None:
        """Two identical calls produce identical output."""
        f1 = compute_all_cross_features(v1_closes, params)
        f2 = compute_all_cross_features(v1_closes, params)
        assert f1 == f2


# ---------------------------------------------------------------------------
# Vector 2 — ETH leadership rotation (surge)
# ---------------------------------------------------------------------------

class TestVector2EthSurge:
    # Pre-computed golden values (analytically derived):
    # RS series (60 values): [0]*40 + [0.01, 0.02, ..., 0.20]
    # mean = 2.10/60 = 0.035
    # current RS = 0.20
    # Σ(ri − mean)^2 = 0.2135  →  std = sqrt(0.2135/59) ≈ 0.060155
    # z = 0.165 / 0.060155 ≈ 2.7429
    _GOLDEN_RS = 0.20
    _GOLDEN_Z = pytest.approx(2.7429, abs=0.001)

    def test_eth_btc_rs_golden(self, v2_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v2_closes, params)
        assert features["eth_btc_rs"] == pytest.approx(self._GOLDEN_RS, rel=1e-9)

    def test_eth_btc_rs_zscore_golden(self, v2_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v2_closes, params)
        assert features["eth_btc_rs_zscore"] == self._GOLDEN_Z

    def test_eth_btc_rs_zscore_crosses_threshold(
        self, v2_closes: pd.DataFrame, params: FeatureParams
    ) -> None:
        """z-score exceeds the 2.0 leadership_rotation threshold from thresholds.yaml."""
        features = compute_all_cross_features(v2_closes, params)
        assert features["eth_btc_rs_zscore"] > 2.0

    def test_sol_hype_unaffected(self, v2_closes: pd.DataFrame, params: FeatureParams) -> None:
        """SOL and HYPE are flat → RS = 0, z-score = 0 (unchanged by ETH surge)."""
        features = compute_all_cross_features(v2_closes, params)
        assert features["sol_btc_rs"] == pytest.approx(0.0, abs=1e-10)
        assert features["hype_btc_rs"] == pytest.approx(0.0, abs=1e-10)
        assert features["sol_btc_rs_zscore"] == 0.0
        assert features["hype_btc_rs_zscore"] == 0.0

    def test_determinism(self, v2_closes: pd.DataFrame, params: FeatureParams) -> None:
        f1 = compute_all_cross_features(v2_closes, params)
        f2 = compute_all_cross_features(v2_closes, params)
        assert f1 == f2


# ---------------------------------------------------------------------------
# Vector 3 — ETH decline (mirror of Vector 2)
# ---------------------------------------------------------------------------

class TestVector3EthDecline:
    # Mirror of Vector 2:
    # RS series: [0]*40 + [-0.01, -0.02, ..., -0.20]
    # mean = -0.035, current RS = -0.20
    # z ≈ -2.7429  (symmetric with Vector 2)
    _GOLDEN_RS = pytest.approx(-0.20, rel=1e-9)
    _GOLDEN_Z = pytest.approx(-2.7429, abs=0.001)

    def test_eth_btc_rs_golden(self, v3_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v3_closes, params)
        assert features["eth_btc_rs"] == self._GOLDEN_RS

    def test_eth_btc_rs_zscore_golden(self, v3_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v3_closes, params)
        assert features["eth_btc_rs_zscore"] == self._GOLDEN_Z

    def test_eth_btc_rs_zscore_below_negative_threshold(
        self, v3_closes: pd.DataFrame, params: FeatureParams
    ) -> None:
        """z-score is below -2.0 (symmetric detection of underperformance)."""
        features = compute_all_cross_features(v3_closes, params)
        assert features["eth_btc_rs_zscore"] < -2.0

    def test_sol_hype_unaffected(self, v3_closes: pd.DataFrame, params: FeatureParams) -> None:
        features = compute_all_cross_features(v3_closes, params)
        assert features["sol_btc_rs"] == pytest.approx(0.0, abs=1e-10)
        assert features["hype_btc_rs"] == pytest.approx(0.0, abs=1e-10)


# ---------------------------------------------------------------------------
# Cross-vector symmetry check
# ---------------------------------------------------------------------------

class TestVectorSymmetry:
    def test_v2_v3_rs_opposite_sign(
        self, v2_closes: pd.DataFrame, v3_closes: pd.DataFrame, params: FeatureParams
    ) -> None:
        """Surge and decline scenarios produce RS values of opposite sign."""
        f2 = compute_all_cross_features(v2_closes, params)
        f3 = compute_all_cross_features(v3_closes, params)
        assert f2["eth_btc_rs"] > 0.0
        assert f3["eth_btc_rs"] < 0.0

    def test_v2_v3_zscore_symmetric(
        self, v2_closes: pd.DataFrame, v3_closes: pd.DataFrame, params: FeatureParams
    ) -> None:
        """Z-scores should be approximately equal in magnitude (opposite sign)."""
        f2 = compute_all_cross_features(v2_closes, params)
        f3 = compute_all_cross_features(v3_closes, params)
        assert f2["eth_btc_rs_zscore"] == pytest.approx(-f3["eth_btc_rs_zscore"], rel=1e-9)

    def test_compute_rs_and_zscore_directly_v2(self, params: FeatureParams) -> None:
        """Direct call to compute_rs_and_zscore matches full pipeline output for Vector 2."""
        eth = pd.Series([1000.0] * _ZSCORE_WIN + [1000.0 + i * 10.0 for i in range(1, _LOOKBACK + 1)])
        btc = pd.Series([50000.0] * _N)
        rs, z = compute_rs_and_zscore(eth, btc, params.rs_lookback, params.rs_zscore_window)
        assert rs == pytest.approx(0.20, rel=1e-9)
        assert z == pytest.approx(2.7429, abs=0.001)
        assert not math.isnan(rs)
        assert not math.isnan(z)
