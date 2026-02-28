"""
Unit tests for RegimeShiftEvaluator (alerts/regime_shift.py).

8 deterministic test vectors (rules.md §5.2):

  V1  Named transition, confidence ≥ 0.5          → fires HIGH
  V2  Same regime continues                        → no fire
  V3  Startup (_current_regime=None)               → no fire, sets baseline
  V4  Uncertain streak reaches 5 cycles            → conditions_met=True, MEDIUM
  V5  Uncertain streak = 4 cycles                  → conditions_met=False (no fire)
  V6  Transition but confidence = 0.3 (below 0.5) → no fire
  V7  INDETERMINATE fires → streak resets → 4 more uncertain cycles → no fire
  V8  regime:latest key missing (cache miss)       → no fire

Plus: params load test.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from alerts.regime_shift import RegimeShiftEvaluator, RegimeShiftParams

_THRESHOLDS_PATH = str(
    Path(__file__).parents[2] / "configs" / "thresholds.yaml"
)

_CYCLE_TIME = datetime(2026, 2, 28, tzinfo=timezone.utc)


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


def _redis_with_regime(
    regime: str | None,
    confidence: float = 0.7,
    inputs: dict | None = None,
) -> AsyncMock:
    r = AsyncMock()
    payload = {"regime": regime, "confidence": confidence, "inputs": inputs or {}}
    r.get.return_value = json.dumps(payload)
    return r


def _redis_missing() -> AsyncMock:
    r = AsyncMock()
    r.get.return_value = None
    return r


def _make_evaluator() -> tuple[RegimeShiftEvaluator, AsyncMock]:
    engine = AsyncMock()
    engine.evaluate_and_fire.return_value = False  # default: not fired
    ev = RegimeShiftEvaluator(_settings_stub(), AsyncMock(), engine)
    return ev, engine


# ---------------------------------------------------------------------------
# Params load test
# ---------------------------------------------------------------------------


def test_params_loads_correct_values() -> None:
    params = RegimeShiftParams.load(_THRESHOLDS_PATH)
    assert params.min_confidence == 0.5
    assert params.indeterminate_streak_threshold == 5


# ---------------------------------------------------------------------------
# V1: Named transition, confidence ≥ 0.5 → fires HIGH
# ---------------------------------------------------------------------------


def test_v1_named_transition_fires_high() -> None:
    """Regime changes from RISK_ON_TREND → DELEVERAGING with high confidence → HIGH alert."""
    ev, engine = _make_evaluator()
    ev._current_regime = "RISK_ON_TREND"
    ev._redis = _redis_with_regime("DELEVERAGING", confidence=0.7)
    _run(ev._evaluate_cycle(_CYCLE_TIME))

    engine.evaluate_and_fire.assert_called_once()
    kwargs = engine.evaluate_and_fire.call_args.kwargs
    assert kwargs["conditions_met"] is True
    assert kwargs["direction"] == "RISK_ON_TREND_to_DELEVERAGING"
    assert kwargs["severity"] == "HIGH"
    assert kwargs["alert_type"] == "REGIME_SHIFT"
    assert kwargs["symbol"] is None


# ---------------------------------------------------------------------------
# V2: Same regime continues → no fire
# ---------------------------------------------------------------------------


def test_v2_same_regime_no_fire() -> None:
    """Regime stays RISK_ON_TREND → evaluate_and_fire never called."""
    ev, engine = _make_evaluator()
    ev._current_regime = "RISK_ON_TREND"
    ev._redis = _redis_with_regime("RISK_ON_TREND", confidence=0.9)
    _run(ev._evaluate_cycle(_CYCLE_TIME))

    engine.evaluate_and_fire.assert_not_called()


# ---------------------------------------------------------------------------
# V3: Startup (_current_regime=None) → no fire, baseline set
# ---------------------------------------------------------------------------


def test_v3_startup_no_fire_sets_baseline() -> None:
    """First cycle after startup: no prior regime → no fire, but _current_regime is set."""
    ev, engine = _make_evaluator()
    assert ev._current_regime is None
    ev._redis = _redis_with_regime("RISK_ON_TREND", confidence=0.8)
    _run(ev._evaluate_cycle(_CYCLE_TIME))

    engine.evaluate_and_fire.assert_not_called()
    assert ev._current_regime == "RISK_ON_TREND"


# ---------------------------------------------------------------------------
# V4: Uncertain streak reaches 5 cycles → conditions_met=True, MEDIUM
# ---------------------------------------------------------------------------


def test_v4_uncertain_streak_5_fires_medium() -> None:
    """After 5 consecutive uncertain cycles, evaluate_and_fire receives conditions_met=True."""
    ev, engine = _make_evaluator()

    for _ in range(5):
        ev._redis = _redis_with_regime(None, confidence=0.2)
        _run(ev._evaluate_cycle(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    assert len(calls) == 5
    last = calls[-1].kwargs
    assert last["conditions_met"] is True
    assert last["severity"] == "MEDIUM"
    assert last["direction"] == "indeterminate"
    assert last["symbol"] is None


# ---------------------------------------------------------------------------
# V5: Uncertain streak = 4 cycles → conditions_met=False on every call
# ---------------------------------------------------------------------------


def test_v5_uncertain_streak_4_no_fire() -> None:
    """4 consecutive uncertain cycles: every call has conditions_met=False."""
    ev, engine = _make_evaluator()

    for _ in range(4):
        ev._redis = _redis_with_regime(None, confidence=0.2)
        _run(ev._evaluate_cycle(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    assert len(calls) == 4
    assert all(not c.kwargs["conditions_met"] for c in calls)


# ---------------------------------------------------------------------------
# V6: Transition but confidence below min_confidence → no fire, baseline updates
# ---------------------------------------------------------------------------


def test_v6_low_confidence_transition_no_fire() -> None:
    """Regime changes but confidence=0.3 (below 0.5) → evaluate_and_fire not called."""
    ev, engine = _make_evaluator()
    ev._current_regime = "RISK_ON_TREND"
    ev._redis = _redis_with_regime("DELEVERAGING", confidence=0.3)
    _run(ev._evaluate_cycle(_CYCLE_TIME))

    engine.evaluate_and_fire.assert_not_called()
    # _current_regime still updated to track latest regime
    assert ev._current_regime == "DELEVERAGING"


# ---------------------------------------------------------------------------
# V7: INDETERMINATE fires → streak resets → 4 more uncertain cycles → no fire
# ---------------------------------------------------------------------------


def test_v7_indeterminate_streak_resets_after_fire() -> None:
    """After INDETERMINATE alert fires on cycle 5, the next 4 uncertain cycles don't reach threshold."""
    ev, engine = _make_evaluator()
    # Cycles 1-4: return False (no fire, streak accumulates)
    # Cycle 5: return True (fired, streak resets to 0)
    # Cycles 6-9: return False (streak 1-4, below threshold)
    engine.evaluate_and_fire.side_effect = [
        False, False, False, False, True,
        False, False, False, False,
    ]

    for _ in range(9):
        ev._redis = _redis_with_regime(None, confidence=0.2)
        _run(ev._evaluate_cycle(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    assert len(calls) == 9
    # 5th call (index 4): streak reached threshold, conditions_met=True
    assert calls[4].kwargs["conditions_met"] is True
    # Calls 5-8 (indices 5-8): streak reset to 0 then 1-4, all below threshold
    for call in calls[5:]:
        assert call.kwargs["conditions_met"] is False


# ---------------------------------------------------------------------------
# V8: regime:latest key missing (cache miss) → no fire
# ---------------------------------------------------------------------------


def test_v8_cache_miss_no_fire() -> None:
    """Redis returns None for regime:latest → evaluate_and_fire never called."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_missing()
    _run(ev._evaluate_cycle(_CYCLE_TIME))

    engine.evaluate_and_fire.assert_not_called()
