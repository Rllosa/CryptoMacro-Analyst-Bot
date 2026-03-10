"""
Tests for AL-8: DELEVERAGING_EVENT Alert Evaluator (SOLO-51)

Pure unit tests — no real network, DB, or Redis calls.
All external dependencies are mocked.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alerts.deleveraging_event import DeleveragingEvaluator, DeleveragingParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CYCLE_TIME = datetime(2026, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
_SYMBOL = "BTCUSDT"

_PARAMS = DeleveragingParams(
    liq_1h_usd_threshold=50_000_000,
    oi_drop_threshold=1.0,
    atr_ratio_threshold=2.0,
)

_DERIV_FIRING = {
    "liquidations_1h_usd": 60_000_000,
    "oi_drop_1h": 1.0,
    "funding_zscore": 2.5,
}
_FEAT_FIRING = {
    "atr_ratio": 2.5,
    "rv_1h": 0.06,
}


def _make_redis(deriv: dict | None, feat: dict | None) -> AsyncMock:
    redis = AsyncMock()

    async def _get(key: str) -> str | None:
        if "derivatives" in key:
            return json.dumps({"time": "2026-03-09T12:00:00", "features": deriv}) if deriv is not None else None
        return json.dumps({"time": "2026-03-09T12:00:00", "features": feat}) if feat is not None else None

    redis.get = _get
    return redis


def _make_evaluator(
    redis: AsyncMock,
    engine: MagicMock,
    event_analyzer: MagicMock | None = None,
) -> DeleveragingEvaluator:
    settings = MagicMock()
    settings.thresholds_path = "configs/thresholds.yaml"
    settings.feature_interval_secs = 300

    ev = DeleveragingEvaluator.__new__(DeleveragingEvaluator)
    ev._settings = settings
    ev._redis = redis
    ev._engine = engine
    ev._event_analyzer = event_analyzer
    ev._params = _PARAMS
    ev._shutdown = asyncio.Event()
    return ev


# ---------------------------------------------------------------------------
# T1 — all 3 conditions met → evaluate_and_fire(conditions_met=True, severity='HIGH')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_all_conditions_met_fires_high() -> None:
    """liq=60M, oi_drop=1.0, atr_ratio=2.5 → fires HIGH."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=True)

    redis = _make_redis(_DERIV_FIRING, _FEAT_FIRING)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    engine.evaluate_and_fire.assert_called_once()
    call_kw = engine.evaluate_and_fire.call_args.kwargs
    assert call_kw["conditions_met"] is True
    assert call_kw["severity"] == "HIGH"
    assert call_kw["alert_type"] == "DELEVERAGING_EVENT"
    assert call_kw["symbol"] == _SYMBOL


# ---------------------------------------------------------------------------
# T2 — oi_drop below threshold → conditions_met=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_oi_drop_below_threshold_no_fire() -> None:
    """liq=60M, oi_drop=0.0, atr_ratio=2.5 → conditions_met=False."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=False)

    deriv = {**_DERIV_FIRING, "oi_drop_1h": 0.0}
    redis = _make_redis(deriv, _FEAT_FIRING)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    call_kw = engine.evaluate_and_fire.call_args.kwargs
    assert call_kw["conditions_met"] is False


# ---------------------------------------------------------------------------
# T3 — liquidations below threshold → conditions_met=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_liq_below_threshold_no_fire() -> None:
    """liq=40M, oi_drop=1.0, atr_ratio=2.5 → conditions_met=False."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=False)

    deriv = {**_DERIV_FIRING, "liquidations_1h_usd": 40_000_000}
    redis = _make_redis(deriv, _FEAT_FIRING)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    call_kw = engine.evaluate_and_fire.call_args.kwargs
    assert call_kw["conditions_met"] is False


# ---------------------------------------------------------------------------
# T4 — atr_ratio below threshold → conditions_met=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_atr_ratio_below_threshold_no_fire() -> None:
    """liq=60M, oi_drop=1.0, atr_ratio=1.5 → conditions_met=False."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=False)

    feat = {**_FEAT_FIRING, "atr_ratio": 1.5}
    redis = _make_redis(_DERIV_FIRING, feat)
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

    redis = _make_redis(None, _FEAT_FIRING)
    ev = _make_evaluator(redis, engine)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    engine.evaluate_and_fire.assert_not_called()


# ---------------------------------------------------------------------------
# T6 — alert fires → event_analyzer.analyze called as background task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t6_fire_triggers_event_analyzer() -> None:
    """When evaluate_and_fire returns True, event_analyzer.analyze is spawned."""
    engine = MagicMock()
    engine.evaluate_and_fire = AsyncMock(return_value=True)

    event_analyzer = MagicMock()
    event_analyzer.analyze = AsyncMock()

    redis = _make_redis(_DERIV_FIRING, _FEAT_FIRING)
    ev = _make_evaluator(redis, engine, event_analyzer)

    await ev._evaluate_symbol(_SYMBOL, _CYCLE_TIME)

    # Allow background task to run
    await asyncio.sleep(0)

    event_analyzer.analyze.assert_called_once()
    call_kw = event_analyzer.analyze.call_args.kwargs
    assert call_kw["alert_type"] == "DELEVERAGING_EVENT"
    assert call_kw["symbol"] == _SYMBOL
    assert call_kw["severity"] == "HIGH"
