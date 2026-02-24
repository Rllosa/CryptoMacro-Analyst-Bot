"""
Tests for the FE-6 Regime Classifier (regime/).

Structure:
  - _eval_condition      — 4 pure-function unit tests
  - classify_regime      — 5 pure-function unit tests
  - RegimeParams         — 1 load test (reads actual thresholds.yaml)
  - _update_regime_tracking — 2 unit tests (transition + continuation)
  - _run_cycle           — 5 integration tests (mocked Redis + pool)
  - insert_regime        — 1 unit test (skips when uncertain)

All async tests use asyncio.run() — consistent with the rest of the test suite.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from regime.classifier import (
    RegimeResult,
    _build_regime_inputs,
    _compute_rv_4h_zscore,
    _eval_condition,
    classify_regime,
)
from regime.config import RegimeParams
from regime.db import insert_regime
from regime.engine import RegimeClassifier, cache_regime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_THRESHOLDS_PATH = str(
    Path(__file__).parents[2] / "configs" / "thresholds.yaml"
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers — minimal params for unit tests (avoids full thresholds.yaml I/O)
# ---------------------------------------------------------------------------


def _params_with(
    base_weight: float = 0.4,
    condition_weight: float = 0.15,
    min_confidence: float = 0.4,
    regimes: dict | None = None,
) -> RegimeParams:
    return RegimeParams(
        base_weight=base_weight,
        condition_weight=condition_weight,
        zscore_bonus_threshold=3.0,
        zscore_bonus=0.1,
        min_confidence=min_confidence,
        regimes=regimes or {},
        tight_bb_bandwidth_max=0.03,
    )


def _risk_on_regime_cfg() -> dict:
    return {
        "primary_condition": {"field": "btc_trend", "operator": ">", "value": 0},
        "additional_conditions": [
            {"field": "volatility_regime", "operator": "==", "value": "low"},
            {"field": "macro_stress", "operator": "<", "value": 40},
            {"field": "funding_zscore", "operator": "<", "value": 2.0},
            {"field": "btc_spx_correlation", "operator": ">", "value": 0.3},
        ],
    }


def _vol_expansion_regime_cfg() -> dict:
    return {
        "primary_condition": {"field": "rv_4h_zscore", "operator": ">=", "value": 2.0},
        "additional_conditions": [
            {"field": "volume_zscore", "operator": ">", "value": 1.0},
            {"field": "candle_size", "operator": ">", "value": 1.5},
            {"field": "vix", "operator": ">", "value": 20},
        ],
    }


def _deleveraging_regime_cfg() -> dict:
    return {
        "primary_condition": {
            "field": "liquidations_1h_usd", "operator": ">=", "value": 50_000_000
        },
        "additional_conditions": [
            {"field": "oi_drop_1h", "operator": ">=", "value": 0.05},
            {"field": "funding_zscore", "operator": ">", "value": 2.0},
            {"field": "candle_size", "operator": ">", "value": 2.0},
        ],
    }


def _chop_range_regime_cfg() -> dict:
    return {
        "primary_condition": {"field": "price_range", "operator": "tight", "value": True},
        "additional_conditions": [
            {"field": "volatility_regime", "operator": "==", "value": "low"},
            {"field": "volume_zscore", "operator": "<", "value": 0.5},
            {"field": "breakout_flags", "operator": "all_false", "value": True},
        ],
    }


def _base_inputs(**overrides) -> dict:
    """Neutral inputs — no primary condition met for any regime."""
    base = {
        "btc_trend": 0.0,
        "volatility_regime": "low",
        "rv_4h_zscore": 0.0,
        "macro_stress": 0.0,
        "volume_zscore": 0.0,
        "price_range": "wide",
        "breakout_flags": [0.0, 0.0, 0.0, 0.0],
        "candle_size": 0.0,
        "vix": 0.0,
        "btc_spx_correlation": 0.0,
        "dxy_momentum": 0.0,
        "funding_zscore": 0.0,
        "liquidations_1h_usd": 0.0,
        "oi_drop_1h": 0.0,
    }
    base.update(overrides)
    return base


def _make_classifier() -> RegimeClassifier:
    settings = MagicMock()
    settings.thresholds_path = _THRESHOLDS_PATH
    settings.feature_interval_secs = 300
    return RegimeClassifier(settings, AsyncMock(), AsyncMock())


def _redis_stub(btc_features: dict, cross_features: dict | None = None) -> AsyncMock:
    """Redis mock returning btcusdt and cross features from dict inputs."""
    cross = cross_features or {}

    async def _get(key):
        if key == "features:latest:btcusdt":
            return json.dumps({"time": "2026-02-20T00:00:00", "features": btc_features})
        if key == "cross_features:latest":
            return json.dumps({"time": "2026-02-20T00:00:00", "features": cross})
        return None

    redis = AsyncMock()
    redis.get = _get
    return redis


# ---------------------------------------------------------------------------
# _eval_condition unit tests
# ---------------------------------------------------------------------------


def test_eval_gt_returns_true_when_met() -> None:
    assert _eval_condition({"rv_4h_zscore": 2.5}, "rv_4h_zscore", ">", 2.0) is True


def test_eval_gte_returns_true_at_boundary() -> None:
    assert _eval_condition({"macro_stress": 60.0}, "macro_stress", ">=", 60) is True


def test_eval_tight_operator_price_range() -> None:
    assert _eval_condition({"price_range": "tight"}, "price_range", "tight", True) is True
    assert _eval_condition({"price_range": "wide"}, "price_range", "tight", True) is False


def test_eval_all_false_operator_breakout_flags() -> None:
    clean = {"breakout_flags": [0.0, 0.0, 0.0, 0.0]}
    dirty = {"breakout_flags": [1.0, 0.0, 0.0, 0.0]}
    assert _eval_condition(clean, "breakout_flags", "all_false", True) is True
    assert _eval_condition(dirty, "breakout_flags", "all_false", True) is False


# ---------------------------------------------------------------------------
# classify_regime unit tests
# ---------------------------------------------------------------------------


def test_no_primary_condition_met_returns_uncertain() -> None:
    params = _params_with(regimes={"RISK_ON_TREND": _risk_on_regime_cfg()})
    # btc_trend=0.0, not > 0 → primary fails
    result = classify_regime(_base_inputs(btc_trend=0.0), params)
    assert result.regime is None


def test_below_min_confidence_returns_uncertain() -> None:
    # base_weight=0.3 < min_confidence=0.4 → uncertain even when primary met
    params = _params_with(
        base_weight=0.3,
        min_confidence=0.4,
        regimes={"RISK_ON_TREND": _risk_on_regime_cfg()},
    )
    # Additional conditions all fail: macro_stress high, funding_zscore high,
    # btc_spx_correlation low, volatility_regime "high" → confidence = 0.3 only
    inputs = _base_inputs(
        btc_trend=0.01,           # primary met
        volatility_regime="high", # != "low" → no bonus
        macro_stress=50.0,        # not < 40 → no bonus
        funding_zscore=3.0,       # not < 2.0 → no bonus
        btc_spx_correlation=0.0,  # not > 0.3 → no bonus
    )
    result = classify_regime(inputs, params)
    assert result.regime is None
    assert pytest.approx(result.confidence, abs=1e-9) == 0.3


def test_risk_on_trend_detected_with_correct_confidence() -> None:
    params = _params_with(regimes={"RISK_ON_TREND": _risk_on_regime_cfg()})
    # primary met + 3 additional conditions → 0.4 + 3*0.15 = 0.85
    inputs = _base_inputs(
        btc_trend=0.01,
        volatility_regime="low",    # == "low" → +0.15
        macro_stress=30.0,          # < 40    → +0.15
        funding_zscore=1.5,         # < 2.0   → +0.15
        btc_spx_correlation=0.0,    # not > 0.3 → no bonus
    )
    result = classify_regime(inputs, params)
    assert result.regime == "RISK_ON_TREND"
    assert pytest.approx(result.confidence, abs=1e-9) == 0.85


def test_vol_expansion_detected() -> None:
    params = _params_with(regimes={"VOL_EXPANSION": _vol_expansion_regime_cfg()})
    inputs = _base_inputs(rv_4h_zscore=2.5)  # >= 2.0 → primary met → 0.4
    result = classify_regime(inputs, params)
    assert result.regime == "VOL_EXPANSION"
    assert pytest.approx(result.confidence, abs=1e-9) == 0.4


def test_tiebreak_priority_deleveraging_over_vol_expansion() -> None:
    """Both regimes at same confidence → DELEVERAGING wins (higher priority)."""
    params = _params_with(
        regimes={
            "VOL_EXPANSION": _vol_expansion_regime_cfg(),
            "DELEVERAGING": _deleveraging_regime_cfg(),
        }
    )
    # Both primary conditions met, no additional conditions met → both at 0.4
    inputs = _base_inputs(
        rv_4h_zscore=2.5,           # VOL_EXPANSION primary
        liquidations_1h_usd=60_000_000,  # DELEVERAGING primary
        btc_trend=-0.01,            # keep RISK_ON_TREND out
    )
    result = classify_regime(inputs, params)
    assert result.regime == "DELEVERAGING"


# ---------------------------------------------------------------------------
# RegimeParams load test
# ---------------------------------------------------------------------------


def test_params_loads_from_thresholds_yaml() -> None:
    params = RegimeParams.load(_THRESHOLDS_PATH)
    assert params.base_weight == pytest.approx(0.4)
    assert params.condition_weight == pytest.approx(0.15)
    assert params.zscore_bonus_threshold == pytest.approx(3.0)
    assert params.zscore_bonus == pytest.approx(0.1)
    assert params.min_confidence == pytest.approx(0.4)
    assert params.tight_bb_bandwidth_max == pytest.approx(0.03)
    assert len(params.regimes) == 5


# ---------------------------------------------------------------------------
# _update_regime_tracking unit tests
# ---------------------------------------------------------------------------


def test_regime_transition_sets_previous_and_duration() -> None:
    classifier = _make_classifier()
    dt1 = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 2, 20, 12, 5, 0, tzinfo=timezone.utc)

    result1 = RegimeResult(regime="RISK_ON_TREND", confidence=0.7, contributing_factors={})
    result2 = RegimeResult(regime="VOL_EXPANSION", confidence=0.6, contributing_factors={})

    prev1, dur1 = classifier._update_regime_tracking(result1, dt1)
    prev2, dur2 = classifier._update_regime_tracking(result2, dt2)

    assert prev1 is None       # first regime — no previous
    assert dur1 == 0            # just started
    assert prev2 == "RISK_ON_TREND"
    assert dur2 == 5            # 5 minutes elapsed


def test_regime_continuation_returns_running_duration() -> None:
    classifier = _make_classifier()
    dt1 = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 2, 20, 12, 15, 0, tzinfo=timezone.utc)

    r = RegimeResult(regime="CHOP_RANGE", confidence=0.5, contributing_factors={})

    classifier._update_regime_tracking(r, dt1)
    prev2, dur2 = classifier._update_regime_tracking(r, dt2)

    assert prev2 is None   # same regime, no transition
    assert dur2 == 15       # 15 minutes since start


# ---------------------------------------------------------------------------
# _run_cycle integration tests (mocked Redis + pool)
# ---------------------------------------------------------------------------


def test_cycle_btc_cache_miss_skips_without_error() -> None:
    """Redis.get returns None for btcusdt → no insert or cache write."""
    classifier = _make_classifier()
    classifier._redis = AsyncMock()
    classifier._redis.get = AsyncMock(return_value=None)
    pool = AsyncMock()
    classifier._pool = pool

    _run(classifier._run_cycle())

    pool.execute.assert_not_called()
    classifier._redis.set.assert_not_called()


def test_cycle_uncertain_result_skips_db_write() -> None:
    """When regime=None, pool.execute is NOT called but Redis IS written."""
    classifier = _make_classifier()
    # Explicit bb values giving wide bandwidth (0.2 > 0.03) → price_range="wide"
    # so CHOP_RANGE primary fails. r_1h=0.0 (not > 0) → RISK_ON_TREND fails.
    # All other primaries also fail with defaults → uncertain.
    btc = {
        "r_1h": 0.0,
        "rv_1h": 0.01,
        "bb_upper": 1.1,
        "bb_lower": 0.9,
        "bb_mid": 1.0,
    }
    classifier._redis = _redis_stub(btc)
    pool = AsyncMock()
    classifier._pool = pool

    _run(classifier._run_cycle())

    pool.execute.assert_not_called()
    classifier._redis.set.assert_called_once()


def test_cycle_regime_detected_calls_db_insert() -> None:
    """When regime is classified (btc_trend > 0 → RISK_ON_TREND), pool.execute is called."""
    classifier = _make_classifier()
    # r_1h > 0 → btc_trend > 0 → RISK_ON_TREND primary met → confidence=0.4 >= 0.4
    btc = {"r_1h": 0.01, "rv_1h": 0.01}
    classifier._redis = _redis_stub(btc)
    pool = AsyncMock()
    classifier._pool = pool

    _run(classifier._run_cycle())

    pool.execute.assert_called_once()


def test_cycle_always_writes_redis_regardless_of_regime() -> None:
    """Redis.set is called every cycle, whether regime is None or classified."""
    classifier = _make_classifier()
    btc = {"r_1h": 0.0, "rv_1h": 0.01}  # uncertain
    classifier._redis = _redis_stub(btc)
    pool = AsyncMock()
    classifier._pool = pool

    _run(classifier._run_cycle())

    # Redis.set must be called for regime:latest
    set_calls = [c for c in classifier._redis.set.call_args_list
                 if c.args and c.args[0] == "regime:latest"]
    assert len(set_calls) == 1


def test_cycle_rv_buffer_grows_each_cycle() -> None:
    """Each cycle with a valid rv_1h value adds one entry to the buffer."""
    classifier = _make_classifier()
    btc = {"r_1h": 0.0, "rv_1h": 0.02}
    classifier._redis = _redis_stub(btc)
    classifier._pool = AsyncMock()

    assert len(classifier._rv_4h_buffer) == 0
    _run(classifier._run_cycle())
    assert len(classifier._rv_4h_buffer) == 1
    _run(classifier._run_cycle())
    assert len(classifier._rv_4h_buffer) == 2


# ---------------------------------------------------------------------------
# insert_regime unit test
# ---------------------------------------------------------------------------


def test_insert_skips_when_regime_is_none() -> None:
    pool = AsyncMock()
    result = RegimeResult(regime=None, confidence=0.3, contributing_factors={})
    _run(insert_regime(pool, datetime.now(timezone.utc), result, None, None))
    pool.execute.assert_not_called()
