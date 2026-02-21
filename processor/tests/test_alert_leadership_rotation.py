"""
Unit and integration tests for LeadershipRotationEvaluator (alerts/leadership_rotation.py).

Structure:
  - LeadershipRotationParams — 1 load test (reads actual thresholds.yaml)
  - _evaluate               — 11 integration tests (mocked Redis + mocked engine)

All async tests use asyncio.run() — consistent with the rest of the test suite.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from alerts.leadership_rotation import (
    LeadershipRotationEvaluator,
    LeadershipRotationParams,
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


def _make_evaluator() -> tuple[LeadershipRotationEvaluator, AsyncMock]:
    engine = AsyncMock()
    ev = LeadershipRotationEvaluator(_settings_stub(), AsyncMock(), engine)
    return ev, engine


def _all_pairs_features(
    eth_btc_rs_zscore: float | None = 0.0,
    sol_btc_rs_zscore: float | None = 0.0,
    hype_btc_rs_zscore: float | None = 0.0,
    eth_btc_rs: float = 0.01,
    sol_btc_rs: float = 0.01,
    hype_btc_rs: float = 0.01,
) -> dict:
    return {
        "eth_btc_rs": eth_btc_rs,
        "eth_btc_rs_zscore": eth_btc_rs_zscore,
        "sol_btc_rs": sol_btc_rs,
        "sol_btc_rs_zscore": sol_btc_rs_zscore,
        "hype_btc_rs": hype_btc_rs,
        "hype_btc_rs_zscore": hype_btc_rs_zscore,
        "macro_stress": 0.0,
    }


# ---------------------------------------------------------------------------
# LeadershipRotationParams load test
# ---------------------------------------------------------------------------


def test_params_loads_correct_threshold() -> None:
    params = LeadershipRotationParams.load(_THRESHOLDS_PATH)
    assert params.rs_zscore_threshold == 2.0


# ---------------------------------------------------------------------------
# _evaluate integration tests
# ---------------------------------------------------------------------------


def test_positive_zscore_alt_over_btc_conditions_true() -> None:
    """eth_btc_rs_zscore=2.1 → eth_over_btc call has conditions_met=True."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(eth_btc_rs_zscore=2.1))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    eth_over_btc = next(c for c in calls if c.kwargs["direction"] == "eth_over_btc")
    assert eth_over_btc.kwargs["conditions_met"] is True


def test_negative_zscore_btc_over_alt_conditions_true() -> None:
    """eth_btc_rs_zscore=-2.1 → btc_over_eth call has conditions_met=True."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(eth_btc_rs_zscore=-2.1))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    btc_over_eth = next(c for c in calls if c.kwargs["direction"] == "btc_over_eth")
    assert btc_over_eth.kwargs["conditions_met"] is True


def test_zscore_below_threshold_conditions_false() -> None:
    """eth_btc_rs_zscore=1.9 → both eth directions have conditions_met=False."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(eth_btc_rs_zscore=1.9))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    eth_calls = [c for c in calls if "eth" in c.kwargs["direction"]]
    assert all(not c.kwargs["conditions_met"] for c in eth_calls)


def test_at_exact_threshold_conditions_true() -> None:
    """eth_btc_rs_zscore=2.0 (exactly at threshold) → conditions_met=True."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(eth_btc_rs_zscore=2.0))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    eth_over_btc = next(c for c in calls if c.kwargs["direction"] == "eth_over_btc")
    assert eth_over_btc.kwargs["conditions_met"] is True


def test_none_zscore_skips_pair() -> None:
    """eth_btc_rs_zscore=None → no calls for eth pair, sol/hype still evaluated."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(eth_btc_rs_zscore=None))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    directions = [c.kwargs["direction"] for c in calls]
    assert not any("eth" in d for d in directions)
    # sol and hype still produce 4 calls
    assert len(calls) == 4


def test_cache_miss_no_calls() -> None:
    """Redis.get returns None → evaluate_and_fire never called."""
    ev, engine = _make_evaluator()
    ev._redis = AsyncMock()
    ev._redis.get.return_value = None
    _run(ev._evaluate(_CYCLE_TIME))

    engine.evaluate_and_fire.assert_not_called()


def test_six_calls_per_cycle() -> None:
    """All 3 pairs with valid zscores → exactly 6 evaluate_and_fire calls."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(
        eth_btc_rs_zscore=1.0,
        sol_btc_rs_zscore=1.0,
        hype_btc_rs_zscore=1.0,
    ))
    _run(ev._evaluate(_CYCLE_TIME))

    assert engine.evaluate_and_fire.call_count == 6


def test_symbol_is_always_none() -> None:
    """Every evaluate_and_fire call must have symbol=None (cross-asset alert)."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(
        eth_btc_rs_zscore=1.0,
        sol_btc_rs_zscore=1.0,
        hype_btc_rs_zscore=1.0,
    ))
    _run(ev._evaluate(_CYCLE_TIME))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["symbol"] is None


def test_severity_is_always_medium() -> None:
    """Every call must pass severity='MEDIUM' regardless of zscore magnitude."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(
        eth_btc_rs_zscore=5.0,
        sol_btc_rs_zscore=-5.0,
        hype_btc_rs_zscore=5.0,
    ))
    _run(ev._evaluate(_CYCLE_TIME))

    for call in engine.evaluate_and_fire.call_args_list:
        assert call.kwargs["severity"] == "MEDIUM"


def test_sol_pair_direction_strings() -> None:
    """sol_btc_rs_zscore=2.5 → directions 'sol_over_btc' (True) and 'btc_over_sol' (False)."""
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(_all_pairs_features(sol_btc_rs_zscore=2.5))
    _run(ev._evaluate(_CYCLE_TIME))

    calls = engine.evaluate_and_fire.call_args_list
    sol_over_btc = next((c for c in calls if c.kwargs["direction"] == "sol_over_btc"), None)
    btc_over_sol = next((c for c in calls if c.kwargs["direction"] == "btc_over_sol"), None)

    assert sol_over_btc is not None
    assert sol_over_btc.kwargs["conditions_met"] is True
    assert btc_over_sol is not None
    assert btc_over_sol.kwargs["conditions_met"] is False


def test_partial_pairs_available() -> None:
    """Only eth zscore present → exactly 2 calls (eth only, sol/hype skipped)."""
    features = {
        "eth_btc_rs": 0.01,
        "eth_btc_rs_zscore": 2.5,
        "macro_stress": 0.0,
        # sol and hype keys absent entirely
    }
    ev, engine = _make_evaluator()
    ev._redis = _redis_with(features)
    _run(ev._evaluate(_CYCLE_TIME))

    assert engine.evaluate_and_fire.call_count == 2
    directions = {c.kwargs["direction"] for c in engine.evaluate_and_fire.call_args_list}
    assert directions == {"eth_over_btc", "btc_over_eth"}
