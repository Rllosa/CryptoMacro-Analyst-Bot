"""
Unit tests for FE-3: Macro Stress Composite (indicators.py + engine._fetch_macro_inputs).

8 deterministic vectors for compute_macro_features() (pure function — no I/O):

  V1  vix=25.0, dxy flat (0% 5d change)            → macro_stress=50.0 exactly
  V2  vix=10.0 (min), dxy -5% 5d change            → macro_stress=0.0 exactly
  V3  vix=40.0 (max), dxy +5% 5d change            → macro_stress=100.0 exactly
  V4  vix=None (missing)                            → no crash, vix output=0.0, macro_stress>0
  V5  dxy_current=None, dxy_5d_ago=None            → dxy_momentum=0.0, no crash
  V6  vix=60.0 (above max → clamp)                 → same macro_stress as vix=40.0 case
  V7  vix=5.0 (below min → clamp)                  → same macro_stress as vix=10.0 case
  V8  params load test                              → vix_weight=0.6, dxy_weight=0.4

Plus 1 engine-level test:
  E1  _fetch_macro_inputs() with Redis returning None for both keys → (None, None, None)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cross_features.indicators import MacroStressParams, compute_macro_features

_THRESHOLDS_PATH = str(
    Path(__file__).parents[2] / "configs" / "thresholds.yaml"
)


# ---------------------------------------------------------------------------
# Shared params fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def params() -> MacroStressParams:
    return MacroStressParams.load(_THRESHOLDS_PATH)


# ---------------------------------------------------------------------------
# V1: mid-range VIX, DXY flat → macro_stress = 50.0
# ---------------------------------------------------------------------------


def test_v1_mid_vix_flat_dxy(params: MacroStressParams) -> None:
    """vix=25 (mid), dxy flat → vix_norm=50, dxy_stress=50 → macro_stress=50.0."""
    result = compute_macro_features(25.0, 100.0, 100.0, params)
    assert result["macro_stress"] == pytest.approx(50.0)
    assert result["vix"] == pytest.approx(25.0)
    assert result["dxy_momentum"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# V2: VIX at min, DXY at min momentum → macro_stress = 0.0
# ---------------------------------------------------------------------------


def test_v2_min_vix_min_dxy_momentum(params: MacroStressParams) -> None:
    """vix=10 (min), dxy -5% → vix_norm=0, dxy_stress=0 → macro_stress=0.0."""
    result = compute_macro_features(10.0, 95.0, 100.0, params)
    assert result["macro_stress"] == pytest.approx(0.0)
    assert result["dxy_momentum"] == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# V3: VIX at max, DXY at max momentum → macro_stress = 100.0
# ---------------------------------------------------------------------------


def test_v3_max_vix_max_dxy_momentum(params: MacroStressParams) -> None:
    """vix=40 (max), dxy +5% → vix_norm=100, dxy_stress=100 → macro_stress=100.0."""
    result = compute_macro_features(40.0, 105.0, 100.0, params)
    assert result["macro_stress"] == pytest.approx(100.0)
    assert result["dxy_momentum"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# V4: vix=None → no crash, vix output=0.0, macro_stress>0 (dxy contributes)
# ---------------------------------------------------------------------------


def test_v4_missing_vix_no_crash(params: MacroStressParams) -> None:
    """vix=None → vix_norm=0.0; dxy still contributes; no exception."""
    result = compute_macro_features(None, 100.0, 99.0, params)
    assert result["vix"] == pytest.approx(0.0)
    # dxy_momentum ≈ 1.01% → dxy_stress > 50 → macro_stress > 0
    assert result["macro_stress"] > 0.0
    assert result["dxy_momentum"] > 0.0


# ---------------------------------------------------------------------------
# V5: both DXY values None → dxy_momentum=0.0, no crash
# ---------------------------------------------------------------------------


def test_v5_missing_dxy_no_crash(params: MacroStressParams) -> None:
    """dxy_current=None, dxy_5d_ago=None → dxy_momentum=0.0; no exception."""
    result = compute_macro_features(20.0, None, None, params)
    assert result["dxy_momentum"] == pytest.approx(0.0)
    assert result["vix"] == pytest.approx(20.0)
    # macro_stress still > 0 because vix=20 contributes
    assert result["macro_stress"] > 0.0


# ---------------------------------------------------------------------------
# V6: vix above max (60.0) → clamped; same macro_stress as vix=40.0
# ---------------------------------------------------------------------------


def test_v6_vix_above_max_clamped(params: MacroStressParams) -> None:
    """vix=60 > 40 (max) → vix_norm clamped to 100; same result as vix=40."""
    result_high = compute_macro_features(60.0, 100.0, 100.0, params)
    result_at_max = compute_macro_features(40.0, 100.0, 100.0, params)
    assert result_high["macro_stress"] == pytest.approx(result_at_max["macro_stress"])


# ---------------------------------------------------------------------------
# V7: vix below min (5.0) → clamped; same macro_stress as vix=10.0
# ---------------------------------------------------------------------------


def test_v7_vix_below_min_clamped(params: MacroStressParams) -> None:
    """vix=5 < 10 (min) → vix_norm clamped to 0; same result as vix=10."""
    result_low = compute_macro_features(5.0, 100.0, 100.0, params)
    result_at_min = compute_macro_features(10.0, 100.0, 100.0, params)
    assert result_low["macro_stress"] == pytest.approx(result_at_min["macro_stress"])


# ---------------------------------------------------------------------------
# V8: params load test
# ---------------------------------------------------------------------------


def test_v8_params_load_correct_weights() -> None:
    """MacroStressParams.load() reads vix_weight=0.6 and dxy_weight=0.4."""
    p = MacroStressParams.load(_THRESHOLDS_PATH)
    assert p.vix_weight == pytest.approx(0.6)
    assert p.dxy_weight == pytest.approx(0.4)
    assert p.vix_min == pytest.approx(10.0)
    assert p.vix_max == pytest.approx(40.0)
    assert p.dxy_momentum_min == pytest.approx(-5.0)
    assert p.dxy_momentum_max == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# E1: _fetch_macro_inputs() returns (None, None, None) when Redis is empty
# ---------------------------------------------------------------------------


def test_e1_fetch_macro_inputs_redis_empty() -> None:
    """Both Redis keys absent + DB returns None → _fetch_macro_inputs returns (None, None, None)."""
    from cross_features.engine import CrossFeatureEngine

    redis = AsyncMock()
    redis.get.return_value = None  # both macro:latest:vix and macro:latest:dxy are None

    pool = AsyncMock()

    settings = MagicMock()
    settings.thresholds_path = _THRESHOLDS_PATH
    settings.feature_interval_secs = 300

    engine = CrossFeatureEngine(settings, pool, redis)

    with patch(
        "cross_features.engine.fetch_dxy_5d_ago", new_callable=AsyncMock, return_value=None
    ):
        vix, dxy_current, dxy_5d_ago = asyncio.run(engine._fetch_macro_inputs())

    assert vix is None
    assert dxy_current is None
    assert dxy_5d_ago is None
