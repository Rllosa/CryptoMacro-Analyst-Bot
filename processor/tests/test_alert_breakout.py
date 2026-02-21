"""
Unit and integration tests for BreakoutEvaluator (alerts/breakout.py).

Structure:
  - BreakoutParams   — 1 load test (reads actual thresholds.yaml)
  - _evaluate_symbol — 11 integration tests (mocked Redis + mocked engine)

All async tests use asyncio.run() — consistent with the rest of the test suite.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from alerts.breakout import (
    BreakoutEvaluator,
    BreakoutParams,
)

_THRESHOLDS_PATH = str(
    Path(__file__).parents[2] / "configs" / "thresholds.yaml"
)

_CYCLE_TIME = datetime(2026, 2, 21, tzinfo=timezone.utc)


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


def _redis_with(features: dict) -> AsyncMock:
    r = AsyncMock()
    r.get.return_value = json.dumps({"time": "2026-02-21T00:00:00", "features": features})
    return r


def _make_evaluator() -> tuple[BreakoutEvaluator, AsyncMock]:
    engine = AsyncMock()
    ev = BreakoutEvaluator(_settings_stub(), AsyncMock(), engine)
    return ev, engine


def _base_features(
    breakout_4h_high: float = 0.0,
    breakout_4h_low: float = 0.0,
    breakout_24h_high: float = 0.0,
    breakout_24h_low: float = 0.0,
    volume_zscore: float = 1.5,
) -> dict:
    return {
        "breakout_4h_high": breakout_4h_high,
        "breakout_4h_low": breakout_4h_low,
        "breakout_24h_high": breakout_24h_high,
        "breakout_24h_low": breakout_24h_low,
        "volume_zscore": volume_zscore,
        "rv_1h": 0.01,
    }


# ---------------------------------------------------------------------------
# BreakoutParams load test
# ---------------------------------------------------------------------------


def test_params_loads_correct_thresholds() -> None:
    params = BreakoutParams.load(_THRESHOLDS_PATH)
    assert params.volume_zscore_min == 1.0
    assert params.severity_4h == "MEDIUM"
    assert params.severity_24h == "HIGH"


# ---------------------------------------------------------------------------
# _evaluate_symbol integration tests
# ---------------------------------------------------------------------------


def test_high_24h_conditions_true() -> None:
    """breakout_24h_high=1.0, volume ok → high_24h conditions_met=True, severity=HIGH."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_base_features(breakout_24h_high=1.0))
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    high_24h = next(c for c in calls if c.kwargs["direction"] == "high_24h")
    assert high_24h.kwargs["conditions_met"] is True
    assert high_24h.kwargs["severity"] == "HIGH"


def test_high_4h_conditions_true() -> None:
    """breakout_4h_high=1.0, no 24h flag, volume ok → high_4h conditions_met=True, severity=MEDIUM."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_base_features(breakout_4h_high=1.0))
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    high_4h = next(c for c in calls if c.kwargs["direction"] == "high_4h")
    assert high_4h.kwargs["conditions_met"] is True
    assert high_4h.kwargs["severity"] == "MEDIUM"


def test_4h_excluded_when_24h_active() -> None:
    """Both high flags set → high_24h conditions_met=True, high_4h conditions_met=False."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_base_features(breakout_4h_high=1.0, breakout_24h_high=1.0))
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    high_24h = next(c for c in calls if c.kwargs["direction"] == "high_24h")
    high_4h = next(c for c in calls if c.kwargs["direction"] == "high_4h")
    assert high_24h.kwargs["conditions_met"] is True
    assert high_4h.kwargs["conditions_met"] is False


def test_low_24h_conditions_true() -> None:
    """breakout_24h_low=1.0, volume ok → low_24h conditions_met=True, severity=HIGH."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_base_features(breakout_24h_low=1.0))
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    low_24h = next(c for c in calls if c.kwargs["direction"] == "low_24h")
    assert low_24h.kwargs["conditions_met"] is True
    assert low_24h.kwargs["severity"] == "HIGH"


def test_low_4h_excluded_when_low_24h_active() -> None:
    """Both low flags set → low_24h conditions_met=True, low_4h conditions_met=False."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_base_features(breakout_4h_low=1.0, breakout_24h_low=1.0))
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    low_24h = next(c for c in calls if c.kwargs["direction"] == "low_24h")
    low_4h = next(c for c in calls if c.kwargs["direction"] == "low_4h")
    assert low_24h.kwargs["conditions_met"] is True
    assert low_4h.kwargs["conditions_met"] is False


def test_volume_below_threshold_conditions_false() -> None:
    """volume_zscore=0.5 < 1.0 → all 4 directions conditions_met=False."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(
        _base_features(breakout_4h_high=1.0, breakout_24h_high=1.0, volume_zscore=0.5)
    )
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["conditions_met"] is False


def test_four_calls_per_symbol() -> None:
    """Every _evaluate_symbol call produces exactly 4 evaluate_and_fire calls."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_base_features(breakout_4h_high=1.0))
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    assert engine.evaluate_and_fire.call_count == 4


def test_symbol_passed_to_engine() -> None:
    """Every evaluate_and_fire call has the correct symbol."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_base_features())
    _run(ev._evaluate_symbol("ETHUSDT", _CYCLE_TIME))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["symbol"] == "ETHUSDT"


def test_cache_miss_no_calls() -> None:
    """Redis.get returns None → evaluate_and_fire never called."""
    ev, engine = _make_evaluator()
    ev._redis = AsyncMock()
    ev._redis.get.return_value = None
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    engine.evaluate_and_fire.assert_not_called()


def test_severity_by_timeframe() -> None:
    """24h directions always receive severity=HIGH; 4h directions severity=MEDIUM."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_base_features())
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    for call in calls:
        direction = call.kwargs["direction"]
        severity = call.kwargs["severity"]
        if "24h" in direction:
            assert severity == "HIGH", f"{direction} should be HIGH"
        else:
            assert severity == "MEDIUM", f"{direction} should be MEDIUM"


def test_missing_volume_zscore_no_calls() -> None:
    """volume_zscore absent from features → evaluate_and_fire never called."""
    ev, engine = _make_evaluator()
    features = {
        "breakout_4h_high": 1.0,
        "breakout_24h_high": 1.0,
        # volume_zscore intentionally absent
    }
    ev._redis = _redis_with(features)
    _run(ev._evaluate_symbol("BTCUSDT", _CYCLE_TIME))

    engine.evaluate_and_fire.assert_not_called()
