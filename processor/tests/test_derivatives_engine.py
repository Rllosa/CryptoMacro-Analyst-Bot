"""
Unit tests for the derivatives feature engine (FE-4).

Structure:
  - DerivativeParams — config parsing
  - indicators.py   — pure functions (funding_zscore, oi_change_pct, oi_drop_1h)
  - derivatives/db  — fetch queries with mock pool
  - DerivativesEngine._process_symbol — Redis key, DB writes, null data handling
  - DerivativesEngine.run()           — shutdown before/after first cycle

All async tests use asyncio.run() — consistent with the rest of the suite.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from derivatives.cache import cache_derivatives
from derivatives.config import DerivativeParams
from derivatives.db import fetch_funding_stats, fetch_latest_snapshot, fetch_oi_1h_ago
from derivatives.engine import DerivativesEngine
from derivatives.indicators import (
    compute_funding_zscore,
    compute_oi_change_pct,
    compute_oi_drop_1h,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 2, 24, 12, 0, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _settings_stub(interval: int = 300):
    s = MagicMock()
    s.feature_interval_secs = interval
    s.thresholds_path = "configs/thresholds.yaml"
    return s


def _params_stub(
    lookback_days: int = 90,
    min_samples: int = 12,
    drop_threshold: float = -0.05,
) -> DerivativeParams:
    return DerivativeParams(
        funding_zscore_lookback_days=lookback_days,
        funding_zscore_min_samples=min_samples,
        oi_drop_threshold_pct=drop_threshold,
    )


def _mock_pool():
    # cur must be an async context manager for `async with conn.cursor() as cur:`
    cur = AsyncMock()
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=None)

    conn = AsyncMock()
    # conn.cursor() must return the cursor directly (not a coroutine)
    conn.cursor = MagicMock(return_value=cur)

    pool = MagicMock()

    @asynccontextmanager
    async def fake_connection():
        yield conn

    pool.connection = fake_connection
    return pool, conn, cur


def _mock_redis():
    return AsyncMock()


def _make_engine(interval: int = 300) -> DerivativesEngine:
    pool, _, _ = _mock_pool()
    redis = _mock_redis()
    engine = DerivativesEngine(_settings_stub(interval), pool, redis)
    engine._params = _params_stub()
    return engine


# ---------------------------------------------------------------------------
# DerivativeParams — config parsing
# ---------------------------------------------------------------------------


def test_derivative_params_from_thresholds_parses_all_fields():
    thresholds = {
        "derivatives_params": {
            "funding_zscore_lookback_days": 90,
            "funding_zscore_min_samples": 12,
            "oi_drop_threshold_pct": -0.05,
        }
    }
    p = DerivativeParams.from_thresholds(thresholds)
    assert p.funding_zscore_lookback_days == 90
    assert p.funding_zscore_min_samples == 12
    assert p.oi_drop_threshold_pct == pytest.approx(-0.05)


def test_derivative_params_load_reads_yaml():
    p = DerivativeParams.load("configs/thresholds.yaml")
    assert p.funding_zscore_lookback_days > 0
    assert p.funding_zscore_min_samples > 0
    assert p.oi_drop_threshold_pct < 0


# ---------------------------------------------------------------------------
# indicators — compute_funding_zscore
# ---------------------------------------------------------------------------


def test_funding_zscore_happy_path():
    z = compute_funding_zscore(0.0002, 0.0001, 0.00005, 20, 12)
    assert z == pytest.approx(2.0)


def test_funding_zscore_insufficient_samples_returns_zero():
    assert compute_funding_zscore(0.0002, 0.0001, 0.00005, 5, 12) == 0.0


def test_funding_zscore_zero_std_returns_zero():
    assert compute_funding_zscore(0.0001, 0.0001, 0.0, 20, 12) == 0.0


def test_funding_zscore_none_std_returns_zero():
    assert compute_funding_zscore(0.0001, 0.0001, None, 20, 12) == 0.0


def test_funding_zscore_none_mean_returns_zero():
    assert compute_funding_zscore(0.0001, None, 0.00005, 20, 12) == 0.0


# ---------------------------------------------------------------------------
# indicators — compute_oi_change_pct
# ---------------------------------------------------------------------------


def test_oi_change_pct_positive():
    result = compute_oi_change_pct(11_000_000.0, 10_000_000.0)
    assert result == pytest.approx(0.10)


def test_oi_change_pct_negative():
    result = compute_oi_change_pct(9_000_000.0, 10_000_000.0)
    assert result == pytest.approx(-0.10)


def test_oi_change_pct_zero_denom_returns_none():
    assert compute_oi_change_pct(5_000_000.0, 0.0) is None


def test_oi_change_pct_none_current_returns_none():
    assert compute_oi_change_pct(None, 10_000_000.0) is None


def test_oi_change_pct_none_historical_returns_none():
    assert compute_oi_change_pct(10_000_000.0, None) is None


# ---------------------------------------------------------------------------
# indicators — compute_oi_drop_1h
# ---------------------------------------------------------------------------


def test_oi_drop_below_threshold_returns_one():
    assert compute_oi_drop_1h(-0.10, -0.05) == 1.0


def test_oi_drop_at_threshold_returns_one():
    assert compute_oi_drop_1h(-0.05, -0.05) == 1.0


def test_oi_drop_above_threshold_returns_zero():
    assert compute_oi_drop_1h(-0.03, -0.05) == 0.0


def test_oi_drop_none_returns_zero():
    assert compute_oi_drop_1h(None, -0.05) == 0.0


# ---------------------------------------------------------------------------
# derivatives/db — fetch queries
# ---------------------------------------------------------------------------


def test_fetch_latest_snapshot_returns_values():
    pool, conn, cur = _mock_pool()
    cur.fetchone.return_value = (0.0001, 5_000_000_000.0, 10_000_000.0)
    avg_f, total_oi, total_liq = _run(fetch_latest_snapshot(pool, "BTC"))
    assert avg_f == pytest.approx(0.0001)
    assert total_oi == pytest.approx(5e9)
    assert total_liq == pytest.approx(1e7)


def test_fetch_latest_snapshot_empty_returns_nones():
    pool, conn, cur = _mock_pool()
    cur.fetchone.return_value = None
    result = _run(fetch_latest_snapshot(pool, "BTC"))
    assert result == (None, None, None)


def test_fetch_oi_1h_ago_returns_value():
    pool, conn, cur = _mock_pool()
    cur.fetchone.return_value = (4_500_000_000.0,)
    result = _run(fetch_oi_1h_ago(pool, "BTC"))
    assert result == pytest.approx(4.5e9)


def test_fetch_oi_1h_ago_no_data_returns_none():
    pool, conn, cur = _mock_pool()
    cur.fetchone.return_value = (None,)
    assert _run(fetch_oi_1h_ago(pool, "BTC")) is None


def test_fetch_funding_stats_returns_mean_std_count():
    pool, conn, cur = _mock_pool()
    cur.fetchone.return_value = (0.0001, 0.00002, 200)
    mean, std, n = _run(fetch_funding_stats(pool, "BTC", 90))
    assert mean == pytest.approx(0.0001)
    assert std == pytest.approx(0.00002)
    assert n == 200


def test_fetch_funding_stats_zero_count_returns_nones():
    pool, conn, cur = _mock_pool()
    cur.fetchone.return_value = (None, None, 0)
    mean, std, n = _run(fetch_funding_stats(pool, "BTC", 90))
    assert mean is None
    assert std is None
    assert n == 0


# ---------------------------------------------------------------------------
# cache_derivatives — Redis key format
# ---------------------------------------------------------------------------


def test_cache_derivatives_uses_correct_key():
    redis = AsyncMock()
    _run(cache_derivatives(redis, "BTC", _NOW, {"funding_zscore": 1.5}))
    call_args = redis.setex.await_args
    key = call_args[0][0]
    assert key == "derivatives:latest:btcusdt"


def test_cache_derivatives_ttl_is_600():
    redis = AsyncMock()
    _run(cache_derivatives(redis, "ETH", _NOW, {"funding_zscore": 0.5}))
    ttl = redis.setex.await_args[0][1]
    assert ttl == 600


# ---------------------------------------------------------------------------
# DerivativesEngine.run() — lifecycle
# ---------------------------------------------------------------------------


def test_shutdown_before_first_cycle_skips_cycle():
    engine = _make_engine(interval=0)
    engine._compute_cycle = AsyncMock()
    engine.request_shutdown()
    _run(engine.run())
    engine._compute_cycle.assert_not_awaited()


def test_shutdown_after_one_cycle_stops_loop():
    engine = _make_engine(interval=0)
    call_count = 0

    async def one_cycle(cycle_time):
        nonlocal call_count
        call_count += 1
        engine.request_shutdown()

    engine._compute_cycle = one_cycle
    _run(engine.run())
    assert call_count == 1


# ---------------------------------------------------------------------------
# DerivativesEngine._process_symbol — symbol failure isolation
# ---------------------------------------------------------------------------


def test_compute_cycle_symbol_failure_does_not_stop_others():
    engine = _make_engine(interval=0)
    called = []

    async def fake_process(sym, cycle_time):
        called.append(sym)
        if sym == "BTC":
            raise RuntimeError("BTC exploded")

    engine._process_symbol = fake_process
    _run(engine._compute_cycle(_NOW))

    # All symbols attempted despite BTC failure
    assert set(called) == {"BTC", "ETH", "SOL", "HYPE"}
