"""
Unit tests for YahooFinanceCollector (yahoo_finance/).

Structure:
  - _is_market_open()    — 3 tests (weekday during hours, outside hours, weekend)
  - upsert_macro_data()  — 3 tests (empty, flat params, row count)
  - _run_cycle()         — 5 tests (writes DB+Redis, partial rows, market closed,
                                    cache key format, TTL)
  - Lifecycle (run())    — 4 tests (shutdown, backfill on startup,
                                    consecutive failures, reset on success)

All async tests use asyncio.run() — consistent with the rest of the suite.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from yahoo_finance.collector import YahooFinanceCollector, _is_market_open
from yahoo_finance.db import upsert_macro_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Tuesday 2026-02-24 15:00 UTC — within market hours (13:30–20:00)
_NOW = datetime(2026, 2, 24, 15, 0, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _settings_stub(poll_interval: int = 300):
    s = MagicMock()
    s.yahoo_poll_interval_secs = poll_interval
    return s


def _mock_pool():
    """Return (pool, mock_conn) where pool is an async context manager stub."""
    conn = AsyncMock()
    pool = MagicMock()

    @asynccontextmanager
    async def fake_connection():
        yield conn

    pool.connection = fake_connection
    return pool, conn


def _make_collector(poll_interval: int = 300, pool=None, redis=None):
    if pool is None:
        pool, _ = _mock_pool()
    if redis is None:
        redis = AsyncMock()
    return YahooFinanceCollector(_settings_stub(poll_interval), pool, redis)


def _canned_rows(n: int = 5) -> list[tuple]:
    indicators = ["DXY", "SPX", "NDX", "VIX", "GOLD"]
    values = [102.5, 5000.0, 17000.0, 15.0, 2600.0]
    return [(_NOW, ind, val, "yahoo") for ind, val in zip(indicators[:n], values[:n])]


# ---------------------------------------------------------------------------
# _is_market_open() — pure function tests
# ---------------------------------------------------------------------------


def test_is_market_open_weekday_during_hours() -> None:
    """Tuesday 15:00 UTC is within 13:30–20:00 market hours."""
    now = datetime(2026, 2, 24, 15, 0, tzinfo=timezone.utc)  # Tuesday
    assert _is_market_open(now) is True


def test_is_market_open_weekday_outside_hours() -> None:
    """Tuesday 10:00 UTC is before 13:30 open — market closed."""
    now = datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc)  # Tuesday
    assert _is_market_open(now) is False


def test_is_market_open_weekend() -> None:
    """Saturday is always closed regardless of time."""
    now = datetime(2026, 2, 21, 15, 0, tzinfo=timezone.utc)  # Saturday
    assert _is_market_open(now) is False


# ---------------------------------------------------------------------------
# upsert_macro_data() — SQL path
# ---------------------------------------------------------------------------


def test_upsert_empty_list_returns_0() -> None:
    pool, conn = _mock_pool()
    result = _run(upsert_macro_data(pool, []))
    assert result == 0
    conn.execute.assert_not_awaited()


def test_upsert_calls_execute_with_flat_params() -> None:
    """execute() receives a flat list, not nested tuples."""
    pool, conn = _mock_pool()
    row = (_NOW, "VIX", 15.0, "yahoo")
    _run(upsert_macro_data(pool, [row]))

    pos_args, _ = conn.execute.await_args
    flat_params = pos_args[1]
    assert flat_params == list(row)


def test_upsert_returns_row_count() -> None:
    pool, conn = _mock_pool()
    rows = [
        (_NOW, "VIX", 15.0, "yahoo"),
        (_NOW, "DXY", 102.5, "yahoo"),
    ]
    result = _run(upsert_macro_data(pool, rows))
    assert result == 2
    conn.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# _run_cycle() — integration tests (asyncio.to_thread mocked)
# ---------------------------------------------------------------------------


def test_cycle_writes_rows_to_db_and_redis() -> None:
    """A full cycle with 5 rows upserts DB once and caches 5 Redis keys."""
    pool, conn = _mock_pool()
    redis = AsyncMock()
    collector = _make_collector(pool=pool, redis=redis)

    rows = _canned_rows(5)
    with patch("yahoo_finance.collector.asyncio.to_thread", new=AsyncMock(return_value=rows)):
        _run(collector._run_cycle(_NOW))

    conn.execute.assert_awaited_once()
    assert redis.set.await_count == 5


def test_cycle_ticker_failure_skips_gracefully() -> None:
    """Partial rows (one ticker missing) still write remaining data."""
    pool, conn = _mock_pool()
    redis = AsyncMock()
    collector = _make_collector(pool=pool, redis=redis)

    # GOLD missing — simulates internal ticker failure inside _fetch_latest
    partial_rows = _canned_rows(4)
    with patch("yahoo_finance.collector.asyncio.to_thread", new=AsyncMock(return_value=partial_rows)):
        _run(collector._run_cycle(_NOW))

    conn.execute.assert_awaited_once()
    assert redis.set.await_count == 4


def test_cycle_skipped_when_market_closed() -> None:
    """When _is_market_open returns False, _run_cycle is never called."""
    collector = _make_collector(poll_interval=0)

    def closed_and_shutdown(now):
        collector.request_shutdown()
        return False

    with patch.object(collector, "_backfill", new=AsyncMock()):
        with patch("yahoo_finance.collector._is_market_open", side_effect=closed_and_shutdown):
            with patch.object(collector, "_run_cycle", new=AsyncMock()) as mock_cycle:
                _run(collector.run())

    mock_cycle.assert_not_awaited()


def test_cache_key_format() -> None:
    """Redis key is macro:latest:{indicator_lowercase}."""
    pool, _ = _mock_pool()
    redis = AsyncMock()
    collector = _make_collector(pool=pool, redis=redis)

    _run(collector._cache_latest([(_NOW, "VIX", 15.0, "yahoo")]))

    redis.set.assert_awaited_once()
    call_args = redis.set.call_args
    assert call_args[0][0] == "macro:latest:vix"


def test_cache_ttl_is_86400() -> None:
    """Redis.set is called with ex=86400 (24 h TTL)."""
    pool, _ = _mock_pool()
    redis = AsyncMock()
    collector = _make_collector(pool=pool, redis=redis)

    _run(collector._cache_latest([(_NOW, "VIX", 15.0, "yahoo")]))

    redis.set.assert_awaited_once()
    call_kwargs = redis.set.call_args[1]
    assert call_kwargs["ex"] == 86400


# ---------------------------------------------------------------------------
# Lifecycle — run() loop tests
# ---------------------------------------------------------------------------


def test_shutdown_before_cycle_exits_cleanly() -> None:
    """Requesting shutdown before run() starts means _run_cycle is never called."""
    collector = _make_collector(poll_interval=0)

    with patch.object(collector, "_backfill", new=AsyncMock()):
        with patch.object(collector, "_run_cycle", new=AsyncMock()) as mock_cycle:
            collector.request_shutdown()
            _run(collector.run())

    mock_cycle.assert_not_awaited()


def test_backfill_called_on_startup() -> None:
    """_backfill() is awaited exactly once before the poll loop begins."""
    collector = _make_collector(poll_interval=0)

    with patch.object(collector, "_backfill", new=AsyncMock()) as mock_backfill:
        collector.request_shutdown()
        _run(collector.run())

    mock_backfill.assert_awaited_once()


def test_consecutive_failures_increments_counter() -> None:
    """_consecutive_failures reaches 3 after three consecutive cycle errors."""
    collector = _make_collector(poll_interval=0)
    call_count = 0

    async def fail_until_shutdown(now):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            collector.request_shutdown()
        raise RuntimeError("API error")

    collector._run_cycle = fail_until_shutdown

    with patch.object(collector, "_backfill", new=AsyncMock()):
        with patch("yahoo_finance.collector._is_market_open", return_value=True):
            _run(collector.run())

    assert collector._consecutive_failures == 3
    assert call_count == 3


def test_consecutive_failure_counter_resets_on_success() -> None:
    """Counter resets to 0 after a successful cycle following failures."""
    collector = _make_collector(poll_interval=0)
    call_count = 0

    async def mixed_cycle(now):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        collector.request_shutdown()

    collector._run_cycle = mixed_cycle

    with patch.object(collector, "_backfill", new=AsyncMock()):
        with patch("yahoo_finance.collector._is_market_open", return_value=True):
            _run(collector.run())

    assert collector._consecutive_failures == 0
