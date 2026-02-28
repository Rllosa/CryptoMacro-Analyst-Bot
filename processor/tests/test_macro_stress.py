"""
Unit tests for the macro stress composite functions in cross_features/indicators.py.

15 deterministic tests — no I/O, no mocks:

_clamp_norm (4 tests):
  T1  Mid-range value → normalized float in (0, 100)
  T2  Value at min bound → 0.0
  T3  Value at max bound → 100.0
  T4  None input → 0.0 safe default

_compute_dxy_momentum (4 tests):
  T5  DXY strengthening → positive float
  T6  DXY weakening → negative float
  T7  dxy_current=None → 0.0
  T8  dxy_5d_ago=0.0 (division-by-zero guard) → 0.0

compute_macro_features golden fixtures (7 tests):
  T9   VIX=40, DXY=+5%   → macro_stress = 100.0  (full stress)
  T10  VIX=10, DXY=-5%   → macro_stress = 0.0    (zero stress)
  T11  VIX=25, DXY=0%    → macro_stress = 50.0   (mid-range)
  T12  VIX=40, DXY=-5%   → macro_stress = 60.0   (RISK_OFF_STRESS threshold exactly)
  T13  VIX=10, DXY=+5%   → macro_stress = 40.0   (DXY contribution only)
  T14  VIX=25, DXY=+5%   → macro_stress = 70.0   (above RISK_OFF_STRESS threshold)
  T15  VIX=None, DXY=None → macro_stress = 0.0   (full data outage → graceful fallback)

Golden fixture formula:
  vix_norm   = clamp((vix - 10) / 30 * 100, 0, 100)
  dxy_stress = clamp((dxy_momentum - (-5)) / 10 * 100, 0, 100)
  macro_stress = vix_norm * 0.6 + dxy_stress * 0.4
"""

from __future__ import annotations

import pytest

from cross_features.indicators import (
    MacroStressParams,
    _clamp_norm,
    _compute_dxy_momentum,
    compute_macro_features,
)

# Inline params — mirrors thresholds.yaml values exactly (vix: 10–40, dxy: −5–+5, weights: 0.6/0.4)
_PARAMS = MacroStressParams(
    vix_weight=0.6,
    dxy_weight=0.4,
    vix_min=10.0,
    vix_max=40.0,
    dxy_momentum_min=-5.0,
    dxy_momentum_max=5.0,
)


# ---------------------------------------------------------------------------
# T1–T4: _clamp_norm
# ---------------------------------------------------------------------------


def test_t1_clamp_norm_mid_range() -> None:
    """Mid-range value (25 in [10, 40]) → 50.0."""
    assert _clamp_norm(25.0, 10.0, 40.0) == pytest.approx(50.0)


def test_t2_clamp_norm_at_min() -> None:
    """Value at min bound → 0.0."""
    assert _clamp_norm(10.0, 10.0, 40.0) == pytest.approx(0.0)


def test_t3_clamp_norm_at_max() -> None:
    """Value at max bound → 100.0."""
    assert _clamp_norm(40.0, 10.0, 40.0) == pytest.approx(100.0)


def test_t4_clamp_norm_none_returns_zero() -> None:
    """None input → 0.0 safe default (graceful degradation on data outage)."""
    assert _clamp_norm(None, 10.0, 40.0) == 0.0


# ---------------------------------------------------------------------------
# T5–T8: _compute_dxy_momentum
# ---------------------------------------------------------------------------


def test_t5_dxy_momentum_strengthening() -> None:
    """DXY rising from 100 to 103 over 5 days → +3% momentum."""
    result = _compute_dxy_momentum(103.0, 100.0)
    assert result == pytest.approx(3.0)


def test_t6_dxy_momentum_weakening() -> None:
    """DXY falling from 100 to 97 → −3% momentum."""
    result = _compute_dxy_momentum(97.0, 100.0)
    assert result == pytest.approx(-3.0)


def test_t7_dxy_momentum_none_current() -> None:
    """Missing current DXY price → 0.0 (no signal)."""
    assert _compute_dxy_momentum(None, 100.0) == 0.0


def test_t8_dxy_momentum_zero_reference() -> None:
    """dxy_5d_ago=0.0 → division-by-zero guard → 0.0."""
    assert _compute_dxy_momentum(100.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# T9–T15: compute_macro_features golden fixtures
# ---------------------------------------------------------------------------


def test_t9_full_stress() -> None:
    """VIX=40 (max), DXY=+5% (max) → macro_stress = 100.0."""
    result = compute_macro_features(40.0, 105.0, 100.0, _PARAMS)
    # dxy_momentum = (105-100)/100*100 = +5%
    assert result["macro_stress"] == pytest.approx(100.0)
    assert result["vix"] == pytest.approx(40.0)
    assert result["dxy_momentum"] == pytest.approx(5.0)


def test_t10_zero_stress() -> None:
    """VIX=10 (min), DXY=−5% (min) → macro_stress = 0.0."""
    result = compute_macro_features(10.0, 95.0, 100.0, _PARAMS)
    # dxy_momentum = (95-100)/100*100 = −5%
    assert result["macro_stress"] == pytest.approx(0.0)


def test_t11_mid_range() -> None:
    """VIX=25 (50%), DXY=0% (50%) → macro_stress = 50.0."""
    # dxy_momentum = 0 → dxy_stress = (0-(-5))/10*100 = 50
    # vix_norm = (25-10)/30*100 = 50
    # macro_stress = 50*0.6 + 50*0.4 = 50.0
    result = compute_macro_features(25.0, 100.0, 100.0, _PARAMS)
    assert result["macro_stress"] == pytest.approx(50.0)


def test_t12_risk_off_stress_threshold() -> None:
    """VIX=40 (max), DXY=−5% (floor) → macro_stress = 60.0 — exactly the RISK_OFF_STRESS threshold.

    Confirms the regime can fire when VIX is at extreme fear even with neutral DXY.
    Threshold from thresholds.yaml: RISK_OFF_STRESS.primary_condition.value = 60.
    """
    # dxy_momentum = −5% → dxy_stress = 0.0
    # vix_norm = 100.0  →  macro_stress = 100*0.6 + 0*0.4 = 60.0
    result = compute_macro_features(40.0, 95.0, 100.0, _PARAMS)
    assert result["macro_stress"] == pytest.approx(60.0)
    assert result["macro_stress"] >= 60.0  # RISK_OFF_STRESS primary condition met


def test_t13_dxy_only() -> None:
    """VIX=10 (floor), DXY=+5% (max) → macro_stress = 40.0 (DXY contribution only)."""
    # vix_norm = 0.0  → dxy_stress = 100.0  → macro_stress = 0*0.6 + 100*0.4 = 40.0
    result = compute_macro_features(10.0, 105.0, 100.0, _PARAMS)
    assert result["macro_stress"] == pytest.approx(40.0)


def test_t14_above_risk_off_threshold() -> None:
    """VIX=25 (50%), DXY=+5% (100%) → macro_stress = 70.0 — clearly above RISK_OFF_STRESS."""
    # vix_norm = 50.0  → dxy_stress = 100.0  → macro_stress = 50*0.6 + 100*0.4 = 70.0
    result = compute_macro_features(25.0, 105.0, 100.0, _PARAMS)
    assert result["macro_stress"] == pytest.approx(70.0)
    assert result["macro_stress"] >= 60.0


def test_t15_full_data_outage() -> None:
    """VIX=None, DXY=None → macro_stress = 20.0, well below RISK_OFF_STRESS threshold (60.0).

    When both inputs are missing:
      vix_norm = 0.0  (None → 0.0 safe default)
      dxy_momentum = 0.0  (None,None → 0.0 safe default)
      dxy_stress = (0.0 - (-5.0)) / 10.0 * 100 = 50.0  (neutral DXY = midpoint of range)
      macro_stress = 0.0 * 0.6 + 50.0 * 0.4 = 20.0

    No false RISK_OFF_STRESS trigger — 20.0 << 60.0 threshold.
    """
    result = compute_macro_features(None, None, None, _PARAMS)
    assert result["macro_stress"] == pytest.approx(20.0)
    assert result["vix"] == pytest.approx(0.0)
    assert result["dxy_momentum"] == pytest.approx(0.0)
    assert result["macro_stress"] < 60.0  # RISK_OFF_STRESS threshold never triggers on outage
