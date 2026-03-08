"""
Tests for LLM-3b: Positioning Bias direction label (SOLO-97)

Pure unit tests of _compute_direction_label — no mocks, no I/O.
The direction label is computed deterministically before the LLM call;
these tests verify the mapping is correct for all regime branches.
"""

from __future__ import annotations

import pytest

from llm.scheduler import _compute_direction_label


# Default thresholds matching thresholds.yaml values
_CONF_HIGH = 0.80
_CONF_MEDIUM = 0.60
_VOL_THRESH = 0.005


def _label(
    regime: str | None,
    confidence: float,
    btc_trend: float = 0.0,
) -> str:
    return _compute_direction_label(
        regime=regime,
        confidence=confidence,
        btc_trend=btc_trend,
        conf_high=_CONF_HIGH,
        conf_medium=_CONF_MEDIUM,
        vol_trend_thresh=_VOL_THRESH,
    )


# ---------------------------------------------------------------------------
# T1 — RISK_ON_TREND, high confidence → "Strongly BULLISH"
# ---------------------------------------------------------------------------


def test_t1_risk_on_trend_high_confidence() -> None:
    """RISK_ON_TREND with confidence ≥ 0.80 → 'Strongly BULLISH'."""
    result = _label("RISK_ON_TREND", confidence=0.92)
    assert result == "Strongly BULLISH"


# ---------------------------------------------------------------------------
# T2 — RISK_ON_TREND, low confidence → "Cautiously BULLISH — thin signal"
# ---------------------------------------------------------------------------


def test_t2_risk_on_trend_low_confidence() -> None:
    """RISK_ON_TREND with confidence < 0.60 → 'Cautiously BULLISH — thin signal'."""
    result = _label("RISK_ON_TREND", confidence=0.42)
    assert result == "Cautiously BULLISH — thin signal"


# ---------------------------------------------------------------------------
# T3 — VOL_EXPANSION, btc_trend > threshold → bullish expansion
# ---------------------------------------------------------------------------


def test_t3_vol_expansion_bullish_trend() -> None:
    """VOL_EXPANSION with btc_trend > 0.005 → 'VOLATILE — bullish expansion'."""
    result = _label("VOL_EXPANSION", confidence=0.70, btc_trend=0.012)
    assert result == "VOLATILE — bullish expansion"


# ---------------------------------------------------------------------------
# T4 — VOL_EXPANSION, btc_trend < -threshold → bearish expansion
# ---------------------------------------------------------------------------


def test_t4_vol_expansion_bearish_trend() -> None:
    """VOL_EXPANSION with btc_trend < -0.005 → 'VOLATILE — bearish expansion'."""
    result = _label("VOL_EXPANSION", confidence=0.70, btc_trend=-0.010)
    assert result == "VOLATILE — bearish expansion"


# ---------------------------------------------------------------------------
# T5 — DELEVERAGING, high confidence → "Strongly BEARISH"
# ---------------------------------------------------------------------------


def test_t5_deleveraging_high_confidence() -> None:
    """DELEVERAGING with confidence ≥ 0.80 → 'Strongly BEARISH'."""
    result = _label("DELEVERAGING", confidence=0.85)
    assert result == "Strongly BEARISH"


# ---------------------------------------------------------------------------
# T6 — None regime → "UNCLEAR — transitioning"
# ---------------------------------------------------------------------------


def test_t6_none_regime_unclear() -> None:
    """None (INDETERMINATE) regime → 'UNCLEAR — transitioning'."""
    result = _label(None, confidence=0.30)
    assert result == "UNCLEAR — transitioning"


# ---------------------------------------------------------------------------
# Additional coverage: medium confidence and VOL_EXPANSION no-direction
# ---------------------------------------------------------------------------


def test_risk_off_stress_medium_confidence() -> None:
    """RISK_OFF_STRESS with 0.60 ≤ confidence < 0.80 → plain 'BEARISH'."""
    result = _label("RISK_OFF_STRESS", confidence=0.65)
    assert result == "BEARISH"


def test_chop_range_low_confidence() -> None:
    """CHOP_RANGE with confidence < 0.60 → 'Cautiously NEUTRAL — thin signal'."""
    result = _label("CHOP_RANGE", confidence=0.45)
    assert result == "Cautiously NEUTRAL — thin signal"


def test_vol_expansion_flat_trend() -> None:
    """VOL_EXPANSION with |btc_trend| ≤ 0.005 → 'VOLATILE — direction unclear'."""
    result = _label("VOL_EXPANSION", confidence=0.75, btc_trend=0.001)
    assert result == "VOLATILE — direction unclear"
