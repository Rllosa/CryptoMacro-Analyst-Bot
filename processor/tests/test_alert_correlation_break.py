"""
Unit tests for CorrelationBreakEvaluator (alerts/correlation_break.py).

11 deterministic test vectors (rules.md §5.2):

  V1   corr_30d=0.7, corr_7d=0.3 → delta_down=0.4      → fires MEDIUM, BTC-SPX_decreasing
  V2   corr_30d=0.7, corr_7d=0.5 → delta=0.2 (below)   → no fire
  V3   corr_30d=0.2, corr_7d=0.6 → delta_up=0.4         → fires MEDIUM, BTC-SPX_increasing
  V4   delta_down=0.3 exactly                            → fires (boundary included)
  V5   BTC-SPX fields missing (FE-3 not running)         → zero calls for that pair
  V6   Cache miss (cross_features:latest = None)         → evaluate_and_fire never called
  V7   Both pairs have breaking correlation               → 4 evaluate_and_fire calls
  V8   symbol=None on every call                         → assert all calls symbol=None
  V9   severity=MEDIUM on every call                     → assert all calls severity="MEDIUM"
  V10  delta_down=0.29 (just under boundary)             → conditions_met=False
  V11  delta_up=0.3, delta_down=0.3 (both at threshold)  → impossible by construction
       (only one direction can be positive at a time — V4 + implied complement)

Plus: params load test.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from alerts.correlation_break import CorrelationBreakEvaluator, CorrelationBreakParams

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


def _redis_with(features: dict) -> AsyncMock:
    r = AsyncMock()
    r.get.return_value = json.dumps({"time": "2026-02-28T00:00:00", "features": features})
    return r


def _redis_missing() -> AsyncMock:
    r = AsyncMock()
    r.get.return_value = None
    return r


def _make_evaluator() -> tuple[CorrelationBreakEvaluator, AsyncMock]:
    engine = AsyncMock()
    engine.evaluate_and_fire.return_value = False
    ev = CorrelationBreakEvaluator(_settings_stub(), AsyncMock(), engine)
    return ev, engine


def _spx_features(corr_30d: float, corr_7d: float) -> dict:
    """BTC-SPX only — BTC-DXY fields absent."""
    return {
        "btc_spx_correlation": corr_30d,
        "btc_spx_correlation_7d": corr_7d,
        "eth_btc_rs": 0.0,
    }


def _both_pairs_features(
    spx_30d: float = 0.5,
    spx_7d: float = 0.5,
    dxy_30d: float = 0.5,
    dxy_7d: float = 0.5,
) -> dict:
    return {
        "btc_spx_correlation": spx_30d,
        "btc_spx_correlation_7d": spx_7d,
        "btc_dxy_correlation": dxy_30d,
        "btc_dxy_correlation_7d": dxy_7d,
    }


# ---------------------------------------------------------------------------
# Params load test
# ---------------------------------------------------------------------------


def test_params_loads_correct_threshold() -> None:
    params = CorrelationBreakParams.load(_THRESHOLDS_PATH)
    assert params.delta_threshold == 0.3


# ---------------------------------------------------------------------------
# V1: corr_30d=0.7, corr_7d=0.3 → delta_down=0.4 → fires BTC-SPX_decreasing
# ---------------------------------------------------------------------------


def test_v1_decreasing_correlation_fires_medium() -> None:
    """corr_30d=0.7 > corr_7d=0.3 → delta_down=0.4 ≥ 0.3 → BTC-SPX_decreasing fires."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_spx_features(corr_30d=0.7, corr_7d=0.3))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    decreasing = next(c for c in calls if c.kwargs["direction"] == "BTC-SPX_decreasing")
    assert decreasing.kwargs["conditions_met"] is True
    assert decreasing.kwargs["severity"] == "MEDIUM"
    assert decreasing.kwargs["alert_type"] == "CORRELATION_BREAK"


# ---------------------------------------------------------------------------
# V2: delta=0.2 (below threshold) → no fire
# ---------------------------------------------------------------------------


def test_v2_small_delta_no_fire() -> None:
    """corr_30d=0.7, corr_7d=0.5 → delta_down=0.2 < 0.3 → conditions_met=False."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_spx_features(corr_30d=0.7, corr_7d=0.5))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    assert all(not c.kwargs["conditions_met"] for c in calls)


# ---------------------------------------------------------------------------
# V3: corr_30d=0.2, corr_7d=0.6 → delta_up=0.4 → fires BTC-SPX_increasing
# ---------------------------------------------------------------------------


def test_v3_increasing_correlation_fires_medium() -> None:
    """corr_7d=0.6 > corr_30d=0.2 → delta_up=0.4 ≥ 0.3 → BTC-SPX_increasing fires."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_spx_features(corr_30d=0.2, corr_7d=0.6))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    increasing = next(c for c in calls if c.kwargs["direction"] == "BTC-SPX_increasing")
    assert increasing.kwargs["conditions_met"] is True


# ---------------------------------------------------------------------------
# V4: delta_down=0.3 exactly (boundary) → fires
# ---------------------------------------------------------------------------


def test_v4_exact_threshold_boundary_fires() -> None:
    """delta_down=0.3 exactly → conditions_met=True (threshold is inclusive)."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_spx_features(corr_30d=0.6, corr_7d=0.3))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    decreasing = next(c for c in calls if c.kwargs["direction"] == "BTC-SPX_decreasing")
    assert decreasing.kwargs["conditions_met"] is True


# ---------------------------------------------------------------------------
# V5: BTC-SPX fields missing → zero calls for that pair
# ---------------------------------------------------------------------------


def test_v5_missing_spx_fields_skips_pair() -> None:
    """BTC-SPX correlation fields absent → no evaluate_and_fire calls for BTC-SPX."""
    ev, engine = _make_evaluator()
    # Only DXY fields present
    ev._redis = _redis_with({
        "btc_dxy_correlation": 0.5,
        "btc_dxy_correlation_7d": 0.5,
    })
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    # Only 2 calls for BTC-DXY (increasing + decreasing), zero for BTC-SPX
    assert len(calls) == 2
    assert all("BTC-DXY" in c.kwargs["direction"] for c in calls)


# ---------------------------------------------------------------------------
# V6: Cache miss → evaluate_and_fire never called
# ---------------------------------------------------------------------------


def test_v6_cache_miss_no_calls() -> None:
    """cross_features:latest missing → evaluate_and_fire never called."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_missing()
    _run(ev._evaluate(_CYCLE_TIME))

    engine.evaluate_and_fire.assert_not_called()


# ---------------------------------------------------------------------------
# V7: Both pairs have breaking correlation → 4 evaluate_and_fire calls
# ---------------------------------------------------------------------------


def test_v7_both_pairs_breaking_four_calls() -> None:
    """Both BTC-SPX and BTC-DXY break threshold → 4 calls (2 directions × 2 pairs)."""
    ev, engine = _make_evaluator()
    # SPX decreasing (delta_down=0.4), DXY increasing (delta_up=0.4)
    ev._redis = _redis_with(_both_pairs_features(
        spx_30d=0.7, spx_7d=0.3,
        dxy_30d=0.1, dxy_7d=0.5,
    ))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    assert len(calls) == 4

    spx_dec = next(c for c in calls if c.kwargs["direction"] == "BTC-SPX_decreasing")
    dxy_inc = next(c for c in calls if c.kwargs["direction"] == "BTC-DXY_increasing")
    assert spx_dec.kwargs["conditions_met"] is True
    assert dxy_inc.kwargs["conditions_met"] is True


# ---------------------------------------------------------------------------
# V8: symbol=None on all calls
# ---------------------------------------------------------------------------


def test_v8_symbol_is_always_none() -> None:
    """All evaluate_and_fire calls must have symbol=None (market-wide alert)."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_both_pairs_features())
    _run(ev._evaluate(_CYCLE_TIME))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["symbol"] is None


# ---------------------------------------------------------------------------
# V9: severity=MEDIUM on all calls
# ---------------------------------------------------------------------------


def test_v9_severity_always_medium() -> None:
    """Every evaluate_and_fire call must pass severity='MEDIUM'."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_both_pairs_features(
        spx_30d=0.8, spx_7d=0.1,
        dxy_30d=0.1, dxy_7d=0.8,
    ))
    _run(ev._evaluate(_CYCLE_TIME))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["severity"] == "MEDIUM"


# ---------------------------------------------------------------------------
# V10: delta_down=0.29 (just under boundary) → conditions_met=False
# ---------------------------------------------------------------------------


def test_v10_just_under_threshold_no_fire() -> None:
    """delta_down=0.29 < 0.30 → conditions_met=False for decreasing direction."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_spx_features(corr_30d=0.59, corr_7d=0.30))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    decreasing = next(c for c in calls if c.kwargs["direction"] == "BTC-SPX_decreasing")
    assert decreasing.kwargs["conditions_met"] is False
