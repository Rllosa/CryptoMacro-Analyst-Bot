"""
Unit and integration tests for VolExpansionEvaluator (alerts/vol_expansion.py).

Structure:
  - _compute_rv_zscore  — 4 pure-function unit tests
  - _classify_severity  — 4 pure-function unit tests
  - VolExpansionParams   — 1 load test (reads actual thresholds.yaml)
  - _evaluate_symbol     — 7 integration tests (mocked Redis + mocked engine)

All async tests use asyncio.run() — consistent with the rest of the test suite.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from alerts.vol_expansion import (
    VolExpansionEvaluator,
    VolExpansionParams,
    _MIN_BUFFER_SAMPLES,
    _compute_rv_zscore,
    _classify_severity,
)

# ---------------------------------------------------------------------------
# Deterministic test buffer
# ---------------------------------------------------------------------------
# _KNOWN_BUFFER: 100 entries — half at 0.005, half at 0.015
#   mean = (50*0.005 + 50*0.015) / 100 = 0.010
#   pstdev = sqrt(sum((x - 0.010)^2 for x in buf) / 100)
#          = sqrt(50*0.000025 + 50*0.000025) / ... = 0.005
# Therefore:
#   rv_1h = 0.020  →  z = (0.020 - 0.010) / 0.005 = 2.0  (exactly at base threshold)
#   rv_1h = 0.0225 →  z = (0.0225 - 0.010) / 0.005 = 2.5 (HIGH escalation threshold)
#   rv_1h = 0.0175 →  z = (0.0175 - 0.010) / 0.005 = 1.5 (below base threshold)

_KNOWN_BUFFER: list[float] = [0.005] * 50 + [0.015] * 50

_RV_AT_Z_2_0 = 0.020
_RV_AT_Z_2_5 = 0.0225
_RV_AT_Z_1_5 = 0.0175

_THRESHOLDS_PATH = str(
    Path(__file__).parents[2] / "configs" / "thresholds.yaml"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _settings_stub():
    s = MagicMock()
    s.thresholds_path = _THRESHOLDS_PATH
    s.feature_interval_secs = 300
    return s


def _make_evaluator(
    buffer_values: list[float] = _KNOWN_BUFFER,
    symbol: str = "BTCUSDT",
) -> tuple[VolExpansionEvaluator, AsyncMock]:
    """Return an evaluator with the engine mocked and the buffer pre-loaded."""
    engine = AsyncMock()
    evaluator = VolExpansionEvaluator(_settings_stub(), AsyncMock(), engine)
    buf = evaluator._rv_buffers[symbol]
    for v in buffer_values:
        buf.append(v)
    return evaluator, engine


def _redis_with_features(features: dict) -> AsyncMock:
    redis = AsyncMock()
    redis.get.return_value = json.dumps({"time": "2026-02-20T00:00:00", "features": features})
    return redis


def _base_features(
    rv_1h: float = _RV_AT_Z_2_0,
    volume_zscore: float = 1.6,
    breakout_4h_high: float = 1.0,
    breakout_4h_low: float = 0.0,
    breakout_24h_high: float = 0.0,
    breakout_24h_low: float = 0.0,
) -> dict:
    return {
        "rv_1h": rv_1h,
        "volume_zscore": volume_zscore,
        "breakout_4h_high": breakout_4h_high,
        "breakout_4h_low": breakout_4h_low,
        "breakout_24h_high": breakout_24h_high,
        "breakout_24h_low": breakout_24h_low,
    }


# ---------------------------------------------------------------------------
# _compute_rv_zscore tests
# ---------------------------------------------------------------------------


def test_zscore_returns_none_below_min_samples() -> None:
    buf: deque[float] = deque(maxlen=288)
    for v in [0.010] * (_MIN_BUFFER_SAMPLES - 1):
        buf.append(v)
    assert _compute_rv_zscore(buf, 0.020) is None


def test_zscore_correct_with_known_buffer() -> None:
    buf: deque[float] = deque(maxlen=288)
    for v in _KNOWN_BUFFER:
        buf.append(v)
    result = _compute_rv_zscore(buf, _RV_AT_Z_2_0)
    assert result == pytest.approx(2.0, abs=1e-9)


def test_zscore_returns_zero_for_constant_buffer() -> None:
    buf: deque[float] = deque(maxlen=288)
    for _ in range(50):
        buf.append(0.010)
    assert _compute_rv_zscore(buf, 0.010) == 0.0


def test_zscore_2_5_with_known_buffer() -> None:
    buf: deque[float] = deque(maxlen=288)
    for v in _KNOWN_BUFFER:
        buf.append(v)
    result = _compute_rv_zscore(buf, _RV_AT_Z_2_5)
    assert result == pytest.approx(2.5, abs=1e-9)


# ---------------------------------------------------------------------------
# _classify_severity tests
# ---------------------------------------------------------------------------


def _default_params() -> VolExpansionParams:
    return VolExpansionParams(
        rv_1h_zscore_threshold=2.0,
        volume_zscore_threshold=1.5,
        high_rv_1h_zscore=2.5,
        high_volume_zscore=2.0,
    )


def test_severity_medium_by_default() -> None:
    params = _default_params()
    assert _classify_severity(params, 2.1, 1.6, False) == "MEDIUM"


def test_severity_high_all_conditions_met() -> None:
    params = _default_params()
    assert _classify_severity(params, 2.5, 2.0, True) == "HIGH"


def test_severity_high_requires_24h_breakout() -> None:
    params = _default_params()
    # rv and vol meet HIGH thresholds but breakout is only 4h
    assert _classify_severity(params, 2.5, 2.0, False) == "MEDIUM"


def test_severity_high_requires_rv_threshold() -> None:
    params = _default_params()
    # rv_zscore below 2.5 → not HIGH even with 24h breakout
    assert _classify_severity(params, 2.4, 2.0, True) == "MEDIUM"


# ---------------------------------------------------------------------------
# VolExpansionParams load test
# ---------------------------------------------------------------------------


def test_params_from_thresholds_correct_values() -> None:
    params = VolExpansionParams.load(_THRESHOLDS_PATH)
    assert params.rv_1h_zscore_threshold == 2.0
    assert params.volume_zscore_threshold == 1.5
    assert params.high_rv_1h_zscore == 2.5
    assert params.high_volume_zscore == 2.0


# ---------------------------------------------------------------------------
# _evaluate_symbol integration tests
# ---------------------------------------------------------------------------


def test_v1_conditions_met_passed_to_engine() -> None:
    """Conditions met: rv_zscore=2.0, vol=1.6, 4h high breakout → conditions_met=True."""
    evaluator, engine = _make_evaluator()
    evaluator._redis = _redis_with_features(_base_features())
    _run(evaluator._evaluate_symbol("BTCUSDT", __import__("datetime").datetime(2026, 2, 20, tzinfo=__import__("datetime").timezone.utc)))

    calls = engine.evaluate_and_fire.call_args_list
    # Two calls: "up" and "down"
    assert len(calls) == 2
    up_call = next(c for c in calls if c.kwargs["direction"] == "up")
    assert up_call.kwargs["conditions_met"] is True


def test_v2_volume_below_threshold_conditions_false() -> None:
    """volume_zscore=1.0 < 1.5 → conditions_met=False for both directions."""
    evaluator, engine = _make_evaluator()
    evaluator._redis = _redis_with_features(_base_features(volume_zscore=1.0))
    _run(evaluator._evaluate_symbol("BTCUSDT", __import__("datetime").datetime(2026, 2, 20, tzinfo=__import__("datetime").timezone.utc)))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["conditions_met"] is False


def test_v3_no_breakout_conditions_false() -> None:
    """No breakout flags set → conditions_met=False for both directions."""
    evaluator, engine = _make_evaluator()
    evaluator._redis = _redis_with_features(
        _base_features(breakout_4h_high=0.0, breakout_4h_low=0.0)
    )
    _run(evaluator._evaluate_symbol("BTCUSDT", __import__("datetime").datetime(2026, 2, 20, tzinfo=__import__("datetime").timezone.utc)))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["conditions_met"] is False


def test_v4_rv_below_threshold_conditions_false() -> None:
    """rv_1h z-score=1.5 < 2.0 → conditions_met=False."""
    evaluator, engine = _make_evaluator()
    evaluator._redis = _redis_with_features(_base_features(rv_1h=_RV_AT_Z_1_5))
    _run(evaluator._evaluate_symbol("BTCUSDT", __import__("datetime").datetime(2026, 2, 20, tzinfo=__import__("datetime").timezone.utc)))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["conditions_met"] is False


def test_v5_cache_miss_engine_not_called() -> None:
    """Redis.get returns None (cache miss) → engine never called."""
    evaluator, engine = _make_evaluator()
    evaluator._redis = AsyncMock()
    evaluator._redis.get.return_value = None
    _run(evaluator._evaluate_symbol("BTCUSDT", __import__("datetime").datetime(2026, 2, 20, tzinfo=__import__("datetime").timezone.utc)))

    engine.evaluate_and_fire.assert_not_called()


def test_v6_severity_high_passed_when_escalation_met() -> None:
    """rv_zscore >> 2.5, vol=2.0, 24h high breakout → severity=HIGH for 'up'.

    Uses rv_1h=0.025 (z≈3.0 with _KNOWN_BUFFER) rather than the boundary value 0.0225
    to avoid floating-point precision issues at the exact threshold boundary.
    """
    evaluator, engine = _make_evaluator()
    evaluator._redis = _redis_with_features(
        _base_features(rv_1h=0.025, volume_zscore=2.0, breakout_24h_high=1.0)
    )
    _run(evaluator._evaluate_symbol("BTCUSDT", __import__("datetime").datetime(2026, 2, 20, tzinfo=__import__("datetime").timezone.utc)))

    calls = engine.evaluate_and_fire.call_args_list
    up_call = next(c for c in calls if c.kwargs["direction"] == "up")
    assert up_call.kwargs["severity"] == "HIGH"


def test_v7_down_direction_uses_low_breakout_flags() -> None:
    """
    breakout_4h_low=1.0, no high breakout →
    - "down" direction: conditions_met=True
    - "up" direction: conditions_met=False
    """
    evaluator, engine = _make_evaluator()
    evaluator._redis = _redis_with_features(
        _base_features(breakout_4h_high=0.0, breakout_4h_low=1.0)
    )
    _run(evaluator._evaluate_symbol("BTCUSDT", __import__("datetime").datetime(2026, 2, 20, tzinfo=__import__("datetime").timezone.utc)))

    calls = engine.evaluate_and_fire.call_args_list
    assert len(calls) == 2
    up_call = next(c for c in calls if c.kwargs["direction"] == "up")
    down_call = next(c for c in calls if c.kwargs["direction"] == "down")
    assert up_call.kwargs["conditions_met"] is False
    assert down_call.kwargs["conditions_met"] is True
