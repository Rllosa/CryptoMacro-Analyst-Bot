"""
Tests for AL-12: NewsEventEvaluator (SOLO-96)

Pure unit tests — no real Redis, DB, NATS, or Claude API calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alerts.news_event import NewsEventEvaluator, _resolve_symbol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.thresholds_path = "configs/thresholds.yaml"
    s.feature_interval_secs = 300
    return s


def _make_signal(
    relevant: bool = True,
    confidence: str = "high",
    direction: str = "bullish",
    event_type: str = "regulatory",
    age_minutes: int = 5,
    assets: list[str] | None = None,
    news_event_id: int = 1,
) -> str:
    return json.dumps({
        "news_event_id": news_event_id,
        "relevant": relevant,
        "direction": direction,
        "confidence": confidence,
        "event_type": event_type,
        "assets": assets if assets is not None else ["BTC"],
        "headline": "SEC approves spot BTC ETF",
        "source": "cryptopanic",
        "age_minutes": age_minutes,
        "reasoning": "ETF approval unlocks institutional demand.",
    })


def _make_evaluator(redis: MagicMock) -> tuple[NewsEventEvaluator, MagicMock]:
    settings = _make_settings()
    mock_engine = AsyncMock()
    mock_engine.evaluate_and_fire = AsyncMock(return_value=True)

    with patch("alerts.news_event.NewsEventParams.load") as mock_load:
        from alerts.news_event import NewsEventParams
        mock_load.return_value = NewsEventParams(
            min_confidence="high",
            max_age_minutes=20,
            excluded_directions=frozenset({"neutral"}),
        )
        evaluator = NewsEventEvaluator(settings, redis, mock_engine)

    return evaluator, mock_engine


# ---------------------------------------------------------------------------
# T1 — Qualifying signal → evaluate_and_fire called once with conditions_met=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_qualifying_signal_fires() -> None:
    """A fresh, high-confidence, relevant signal fires the alert."""
    redis = AsyncMock()
    redis.lrange = AsyncMock(return_value=[_make_signal()])

    evaluator, engine = _make_evaluator(redis)
    await evaluator._evaluate_cycle(datetime.now(tz=timezone.utc))

    engine.evaluate_and_fire.assert_awaited_once()
    call_kwargs = engine.evaluate_and_fire.call_args[1]
    assert call_kwargs["alert_type"] == "NEWS_EVENT"
    assert call_kwargs["conditions_met"] is True
    assert call_kwargs["symbol"] == "BTC"
    assert call_kwargs["direction"] == "regulatory"   # event_type used as dedup direction
    assert call_kwargs["severity"] == "HIGH"


# ---------------------------------------------------------------------------
# T2 — Empty Redis → engine never called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_empty_redis_no_fire() -> None:
    """No signals in Redis → evaluate_and_fire is never called."""
    redis = AsyncMock()
    redis.lrange = AsyncMock(return_value=[])

    evaluator, engine = _make_evaluator(redis)
    await evaluator._evaluate_cycle(datetime.now(tz=timezone.utc))

    engine.evaluate_and_fire.assert_not_called()


# ---------------------------------------------------------------------------
# T3 — Signal too old (age_minutes > max_age_minutes) → engine not called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t3_stale_signal_not_fired() -> None:
    """A signal older than max_age_minutes (20) is ignored."""
    redis = AsyncMock()
    redis.lrange = AsyncMock(return_value=[_make_signal(age_minutes=25)])

    evaluator, engine = _make_evaluator(redis)
    await evaluator._evaluate_cycle(datetime.now(tz=timezone.utc))

    engine.evaluate_and_fire.assert_not_called()


# ---------------------------------------------------------------------------
# T4 — relevant=False → engine not called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_not_relevant_not_fired() -> None:
    """A signal with relevant=False is ignored regardless of other fields."""
    redis = AsyncMock()
    redis.lrange = AsyncMock(return_value=[_make_signal(relevant=False)])

    evaluator, engine = _make_evaluator(redis)
    await evaluator._evaluate_cycle(datetime.now(tz=timezone.utc))

    engine.evaluate_and_fire.assert_not_called()


# ---------------------------------------------------------------------------
# T5 — direction="neutral" (excluded) → engine not called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t5_neutral_direction_excluded() -> None:
    """A neutral-direction signal is excluded and does not fire."""
    redis = AsyncMock()
    redis.lrange = AsyncMock(return_value=[_make_signal(direction="neutral")])

    evaluator, engine = _make_evaluator(redis)
    await evaluator._evaluate_cycle(datetime.now(tz=timezone.utc))

    engine.evaluate_and_fire.assert_not_called()


# ---------------------------------------------------------------------------
# T6 — Two signals same (asset, event_type) → only one evaluate_and_fire call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t6_dedup_same_asset_event_type() -> None:
    """Two qualifying signals for the same (asset, event_type) → one call only."""
    redis = AsyncMock()
    sig1 = _make_signal(news_event_id=1, age_minutes=3)
    sig2 = _make_signal(news_event_id=2, age_minutes=7)
    redis.lrange = AsyncMock(return_value=[sig1, sig2])

    evaluator, engine = _make_evaluator(redis)
    await evaluator._evaluate_cycle(datetime.now(tz=timezone.utc))

    assert engine.evaluate_and_fire.await_count == 1


# ---------------------------------------------------------------------------
# Utility: _resolve_symbol
# ---------------------------------------------------------------------------


def test_resolve_symbol_single_asset() -> None:
    assert _resolve_symbol(["BTC"]) == "BTC"


def test_resolve_symbol_multi_asset() -> None:
    assert _resolve_symbol(["BTC", "ETH"]) == "MARKET"


def test_resolve_symbol_empty() -> None:
    assert _resolve_symbol([]) == "MARKET"
