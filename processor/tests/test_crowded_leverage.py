"""
Tests for AL-7: CROWDED_LEVERAGE Alert Evaluator (SOLO-50)

Pure unit tests — no real network, DB, or Redis calls.
All external dependencies are mocked.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from alerts.crowded_leverage import CrowdedLeverageEvaluator, CrowdedLeverageParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CYCLE_TIME = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
_SYMBOL = "BTCUSDT"

_PARAMS = CrowdedLeverageParams(
    funding_zscore_threshold=2.0,
    funding_zscore_high=3.0,
    oi_change_pct_threshold=5.0,
)

# Firing values — all conditions met, MEDIUM severity
_DERIV_FIRING_MEDIUM = {
    "funding_zscore": 2.5,
    "oi_change_pct": 6.0,
}

# Firing values — HIGH severity (funding_zscore >= 3.0)
_DERIV_FIRING_HIGH = {
    "funding_zscore": 3.2,
    "oi_change_pct": 8.0,
}


def _make_redis(deriv: dict | None) -> AsyncMock:
    redis = AsyncMock()

    async def _get(key: str) -> str | None:
        if deriv is None:
            return None
        return json.dumps({"time": "2026-03-09T12:00:00", "features": deriv})

    redis.get = _get
    return redis


def _make_evaluator(redis: AsyncMock, engine: MagicMock) -> CrowdedLeverageEvaluator:
    settings = MagicMock()
    settings.thresholds_path = "configs/thresholds.yaml"
    settings.feature_interval_secs = 300

    ev = CrowdedLeverageEvaluator.__new__(CrowdedLeverageEvaluator)
    ev._settings = settings
    ev._redis = redis
    ev._engine = engine
    ev._params = _PARAMS
    ev._shutdown = asyncio.Event()
    return ev


# ---------------------------------------------------------------------------
# T1 — both conditions met → evaluate_and_fire(conditions_met=True, severity='MEDIUM')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_both_conditions_met_fires_medium() -> None:
    """funding_zscore=2.5, oi_change_pct=6.0 → fires MEDIUM."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=True)

    redis = _make_redis(_DERIV_FIRING_MEDIUM)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    engine.evaluate_and_fire.assert_called_once()
    call_kw = engine.evaluate_and_fire.call_args.kwargs
    assert call_kw["conditions_met"] is True
    assert call_kw["severity"] == "MEDIUM"
    assert call_kw["alert_type"] == "CROWDED_LEVERAGE"
    assert call_kw["symbol"] == _SYMBOL


# ---------------------------------------------------------------------------
# T2 — funding_zscore >= high threshold → severity HIGH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_high_funding_zscore_escalates_to_high() -> None:
    """funding_zscore=3.2, oi_change_pct=8.0 → fires HIGH."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=True)

    redis = _make_redis(_DERIV_FIRING_HIGH)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    call_kw = engine.evaluate_and_fire.call_args.kwargs
    assert call_kw["conditions_met"] is True
    assert call_kw["severity"] == "HIGH"


# ---------------------------------------------------------------------------
# T3 — funding_zscore below threshold → conditions_met=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_funding_zscore_below_threshold_no_fire() -> None:
    """funding_zscore=1.5, oi_change_pct=6.0 → conditions_met=False."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=False)

    deriv = {**_DERIV_FIRING_MEDIUM, "funding_zscore": 1.5}
    redis = _make_redis(deriv)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    call_kw = engine.evaluate_and_fire.call_args.kwargs
    assert call_kw["conditions_met"] is False


# ---------------------------------------------------------------------------
# T4 — oi_change_pct below threshold → conditions_met=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_oi_change_below_threshold_no_fire() -> None:
    """funding_zscore=2.5, oi_change_pct=3.0 → conditions_met=False."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=False)

    deriv = {**_DERIV_FIRING_MEDIUM, "oi_change_pct": 3.0}
    redis = _make_redis(deriv)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    call_kw = engine.evaluate_and_fire.call_args.kwargs
    assert call_kw["conditions_met"] is False


# ---------------------------------------------------------------------------
# T5 — derivatives cache miss → graceful skip (no engine call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_derivatives_cache_miss_graceful() -> None:
    """Derivatives cache returns None → evaluate_and_fire NOT called."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock()

    redis = _make_redis(None)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    engine.evaluate_and_fire.assert_not_called()


# ---------------------------------------------------------------------------
# T6 — trigger_values passed correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t6_trigger_values_passed_correctly() -> None:
    """trigger_values must contain funding_zscore and oi_change_pct."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=True)

    redis = _make_redis(_DERIV_FIRING_MEDIUM)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    call_kw = engine.evaluate_and_fire.call_args.kwargs
    tv = call_kw["trigger_values"]
    assert tv["funding_zscore"] == pytest.approx(2.5)
    assert tv["oi_change_pct"] == pytest.approx(6.0)
